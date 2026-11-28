from copy import deepcopy
import unittest

from discussion_orchestration import DiscussionEngine, SessionStatus
from spec_handoff import sign_off

from backend.initial_drafts.clarification import ClarificationPolicy, classify_question
from backend.initial_drafts.contracts import ClarificationQuestion, HardConstraint, WorkingDraft, working_draft_id, working_module_id
from backend.initial_drafts.policy import DraftPolicy
from backend.initial_drafts.template import DEFAULT_SOFTWARE_TEMPLATE, DraftTemplate, TemplateField
from backend.initial_drafts.validation import EvidenceContext, InitialDraftQualityGate, SemanticFinding


def item(item_id, text, status="proposed", source_seqs=(), **details):
    return {"id": item_id, "text": text, "status": status, "source_seqs": source_seqs, **details}


def question(index, category):
    return ClarificationQuestion(
        question_id=f"Q-{index}", question=f"需要确认问题 {index} 吗？", category=category,
        reason="影响相关业务决策", source_seqs=(1,),
    )


class WorkingDraftPolicyTest(unittest.TestCase):
    def setUp(self):
        self.gate = InitialDraftQualityGate()
        self.user_constraint = HardConstraint(id="C-1", text="首版只服务总部", origin="user", source_seqs=(1,))
        self.enterprise_constraint = HardConstraint(id="C-2", text="预约记录须保留审计轨迹",
                                                    origin="enterprise", rule_ref="enterprise.audit")
        self.context = EvidenceContext(
            session_id="session", input_version=2, visible_source_seqs=(1, 2, 3), confirmed_source_seqs=(1,),
            required_constraints=(self.user_constraint, self.enterprise_constraint),
        )
        self.bookings = working_module_id("session", "booking")
        self.calendar = working_module_id("session", "calendar")
        self.raw = {
            "session_id": "session", "draft_id": working_draft_id("session", 2, 1),
            "input_version": 2, "revision": 1, "source_seqs": [1, 2],
            "template_ref": {"id": "software-design", "version": 1},
            "policy_ref": {"id": "outline-first", "version": 1},
            "created_at": "2026-11-28T03:15:00Z",
            "content": {
                "product_name": "会议室预约",
                "positioning": item("POSITION", "为总部人员提供会议室预约"),
                "goals": [item("G-1", "查询空闲并预约会议室", "confirmed", (1,))],
                "actors": [item("A-1", "总部人员")],
                "scenarios": [item("S-1", "预约例会", actor_ids=["A-1"], trigger="组织例会", outcome="预订一个时段")],
                "modules": [
                    item(self.bookings, "处理预约和冲突", name="预约", depends_on=[self.calendar]),
                    item(self.calendar, "返回可用时段", name="日历"),
                ],
                "business_flows": [item("FLOW-1", "完成预约", steps=[
                    item("STEP-1", "查询时段", module_id=self.calendar, actor_id="A-1", outcome="显示空闲时段"),
                    item("STEP-2", "提交预约", module_id=self.bookings, actor_id="A-1", outcome="返回预约结果"),
                ])],
                "scope_in": [item("IN-1", "查询和预约")],
                "boundaries": [item("B-1", "仅提供总部房间")],
                "constraints": [self.user_constraint.model_dump(mode="json"), self.enterprise_constraint.model_dump(mode="json")],
                "suggestions": [item("P-1", "冲突时显示备选时段")],
                "assumptions": [item("H-1", "平均提前一周安排例会", "assumed", (2,))],
            },
        }

    def evaluate(self, raw=None, **kwargs):
        return self.gate.evaluate(self.raw if raw is None else raw, self.context, **kwargs)

    def test_complete_draft_and_stable_ids(self):
        draft = WorkingDraft.model_validate(self.raw)
        self.assertEqual(WorkingDraft.model_validate_json(draft.model_dump_json()), draft)
        self.assertEqual(self.evaluate(draft).status, "PASS")
        self.assertEqual(working_module_id("session", "booking"), self.bookings)
        self.assertNotEqual(working_module_id("other", "booking"), self.bookings)
        self.assertNotEqual(working_draft_id("session", 2, 2), draft.draft_id)
        self.assertEqual(draft.as_working_revision().working_schema_version, 2)

    def test_required_field_reports_a_path_and_repair(self):
        del self.raw["content"]["business_flows"]
        result = self.evaluate()
        finding, = result.findings
        self.assertEqual((result.status, finding.rule_id, finding.path),
                         ("INVALID", "template.required", "/content/business_flows"))
        self.assertTrue(finding.suggestion)
        self.assertFalse(result.can_publish)

    def test_duplicate_module_ids_are_rejected(self):
        self.raw["content"]["modules"].append(deepcopy(self.raw["content"]["modules"][0]))
        self.assertIn("modules.duplicate_id", {item.rule_id for item in self.evaluate().findings})

    def test_unknown_references_and_cycles_are_rejected(self):
        self.raw["content"]["modules"][1]["depends_on"] = [self.bookings]
        self.raw["content"]["business_flows"][0]["steps"][0]["module_id"] = "unknown"
        self.raw["content"]["open_questions"] = [question(1, "deferred").model_dump() | {"affected_modules": ("unknown",)}]
        findings = self.evaluate().findings
        self.assertIn("modules.cycle", {item.rule_id for item in findings})
        self.assertEqual(sum(item.rule_id == "reference.unknown_id" for item in findings), 2)

    def test_assumptions_and_unverified_confirmation_are_rejected(self):
        self.raw["content"]["assumptions"][0]["status"] = "confirmed"
        self.assertEqual(self.evaluate().findings[0].path, "/content/assumptions/0/status")
        self.raw["content"]["assumptions"][0]["status"] = "assumed"
        self.raw["content"]["goals"][0]["source_seqs"] = [2]
        self.assertIn("evidence.confirmation_required", {item.rule_id for item in self.evaluate().findings})

    def test_evidence_must_be_visible_and_indexed(self):
        self.raw["content"]["business_flows"][0]["steps"][1]["source_seqs"] = [999]
        self.raw["content"]["modules"][0]["source_seqs"] = [3]
        rules = {item.rule_id for item in self.evaluate().findings}
        self.assertEqual(rules, {"evidence.invalid_reference", "evidence.not_in_draft"})

    def test_versions_and_hard_constraints_cannot_be_omitted_or_invented(self):
        self.raw["input_version"] = 3
        self.raw["draft_id"] = working_draft_id("session", 3, 1)
        self.raw["content"]["constraints"][0]["text"] = "所有办公点"
        self.raw["content"]["constraints"].pop()
        self.assertEqual({item.rule_id for item in self.evaluate().findings},
                         {"draft.snapshot", "constraints.unverified", "constraints.missing"})

    def test_template_can_require_additional_content(self):
        extension = TemplateField(id="support_window", kind="text", title="支持窗口", description="明确支持工作时段。",
                                  required=True, level=2)
        template = DraftTemplate(ref={"id": "support-design", "version": 1},
                                 fields=(*DEFAULT_SOFTWARE_TEMPLATE.fields, extension))
        self.raw["template_ref"] = template.ref.model_dump()
        finding, = self.evaluate(template=template).findings
        self.assertEqual(finding.path, "/content/custom_sections/support_window")
        self.raw["content"]["custom_sections"] = {"support_window": "工作日"}
        self.assertEqual(self.evaluate(template=template).status, "PASS")
        self.raw["content"]["custom_sections"]["support_window"] = ["工作日"]
        self.assertEqual(self.evaluate(template=template).findings[0].rule_id, "template.field_type")

    def test_non_blocking_questions_and_semantic_findings_allow_publication(self):
        self.raw["content"]["open_questions"] = [question(1, "deferred"), question(2, "important")]
        self.assertEqual(self.evaluate().status, "PASS_WITH_WARNINGS")
        finding = SemanticFinding(criterion="risk_coverage", path="/content/risks",
                                  message="需补查并发风险", suggestion="追加失败路径")
        result = self.evaluate(semantic_findings=(finding,))
        self.assertTrue(result.can_publish)
        self.assertTrue(all(not item.blocks_publication for item in result.findings))

    def test_clarification_budget_blocks_without_inventing_answers(self):
        blockers = tuple(question(index, "blocking") for index in range(1, 5))
        policy = DraftPolicy()
        plan = policy.plan((*blockers, question(5, "important"), question(6, "deferred")))
        self.assertEqual((plan.generation_mode, plan.specialist_review_timing), ("outline_first", "after_outline"))
        self.assertEqual(plan.clarification.ask_now, blockers[:3])
        self.assertEqual(plan.clarification.unasked_blocking, blockers[3:])
        self.assertEqual(plan.clarification.after_draft, (question(5, "important"),))
        custom = ClarificationPolicy(max_initial_questions=1).plan(blockers, asked_ids=("Q-1",))
        self.assertFalse(custom.ask_now)
        self.assertEqual(custom.waiting_on, blockers[:1])
        self.assertTrue(custom.publication_blocked)
        self.assertIn("额度", custom.pause_reason)
        self.assertEqual(classify_question(prevents_safe_outline=False, affects_important_decision=False), "deferred")
        self.assertEqual(classify_question(prevents_safe_outline=False, affects_important_decision=True), "important")
        self.assertEqual(classify_question(prevents_safe_outline=True, affects_important_decision=True), "blocking")

    def test_preidentified_blocker_cannot_be_dropped_or_downgraded(self):
        context = EvidenceContext.model_validate({**self.context.model_dump(),
                                                  "pending_questions": (question(1, "blocking"),)})
        self.assertEqual(self.gate.evaluate(self.raw, context).status, "BLOCKED")
        self.raw["content"]["open_questions"] = [question(1, "deferred")]
        result = self.gate.evaluate(self.raw, context)
        self.assertFalse(result.can_publish)
        self.assertIn("clarification.unresolved_blocker", {item.rule_id for item in result.findings})

    def test_working_artifact_cannot_be_approved_or_signed(self):
        draft = WorkingDraft.model_validate(self.raw)
        self.raw["lifecycle"] = "approved"
        self.assertEqual(self.evaluate().status, "INVALID")
        revision = draft.as_working_revision()
        self.assertFalse(revision.current)
        session = DiscussionEngine().create_session(session_id="session", project_id="project", idea="预约")
        session.status = SessionStatus.AWAITING_CONFIRMATION
        session.input_version = 2
        # Simulate accidental placement in the formal candidate collection.
        revision.current = True
        session.drafts.append(revision)
        result = sign_off(session, draft_id=draft.draft_id, input_version=2)
        self.assertFalse(result.ok)
        self.assertIn("工作草稿", result.findings[0].message)
