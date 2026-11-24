"""角色契约边界用例。调用者只提供属于任务快照的消息。"""

import json
from pathlib import Path
import unittest

from backend.roles.contracts import OUTPUT_MODELS, RoleRequest, RoleRuleError, build_messages, validate_output


SPEC = json.loads((Path(__file__).resolve().parents[1] / "backend/fixtures/meeting-discussion-spec.json").read_text(encoding="utf-8"))


def message(seq=7, **changes):
    return dict({"seq": seq, "role": "architect", "text": "并发写入需要检查。", "session_id": "s", "input_version": 1}, **changes)


def task(role="critic", **changes):
    return RoleRequest.model_validate(dict({
        "session_id": "s", "logical_task_id": "t", "role": role, "topic_id": "booking",
        "user_input": "内部会议室预约", "input_version": 2, "messages": [message()],
    }, **changes))


def frozen():
    return dict(id="d2", session_id="s", input_version=2, revision=2, content=SPEC,
                source_map={req["id"]: [7] for req in SPEC["requirements"]})


def issue():
    return dict(issue_id="I-2", severity="blocking", target_item="FR-02", description="缺并发结果", owner_role="architect", closure_condition="两个请求各有可观察结果")


def review(**changes):
    return dict(dict(text="复核并发验收条件。", source_seqs=[7], draft_id="d2", verdict="PASS",
                     findings=[], questions=[]), **changes)


class RoleContractTests(unittest.TestCase):
    def check(self, req, output):
        return validate_output(req, json.dumps(output, ensure_ascii=False))

    def test_nine_role_contracts_and_scoped_prompts(self):
        self.assertEqual(set(OUTPUT_MODELS), {"host", "product", "research", "architect", "designer", "market", "critic", "scribe", "reviewer"})
        prompts = set()
        for role in OUTPUT_MODELS:
            req = task(role, draft=frozen()) if role == "reviewer" else task(role)
            system, user = build_messages(req)
            self.assertEqual((system["role"], user["role"]), ("system", "user"))
            prompts.add(system["content"])
            snapshot = json.loads(user["content"])
            self.assertEqual(snapshot["logical_task_id"], "t")
            self.assertEqual(snapshot["messages"][0]["seq"], 7)
        self.assertEqual(len(prompts), 9)

    def test_request_rejects_cross_session_future_or_ambiguous_sources(self):
        for messages in ([message(session_id="other")], [message(input_version=3)], [message(), message()]):
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                task(messages=messages)

    def test_critic_cites_a_real_message_and_targets_allowed_specialists(self):
        output = dict(text="并发时两人都成功怎么办？", source_seqs=[7], target_roles=["architect"], verification="并发请求并核对写入数")
        req = task(allowed_roles=["architect"])
        self.assertEqual(self.check(req, output).source_seqs, [7])
        for changes in (dict(source_seqs=[]), dict(source_seqs=[99]), dict(target_roles=["host"]), dict(target_roles=["market"]), dict(verification="  ")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(req, dict(output, **changes))

    def test_specialist_evidence_and_market_not_applicable(self):
        output = dict(text="内部工具暂不涉及外部定价。", source_seqs=[7], basis="not_applicable")
        self.check(task("market"), output)
        with self.assertRaises(ValueError):
            self.check(task("research"), output)
        with self.assertRaises(ValueError):
            self.check(task("research"), dict(output, basis="evidence", source_seqs=[]))

    def test_host_references_must_fit_action_and_snapshot(self):
        base = dict(text="请核查冲突场景。", source_seqs=[7], action="DISCUSS", topic_id="booking",
                    target_roles=["architect"], issue_ids=["I-2"], questions=[])
        req = task("host", issues=[issue()], allowed_roles=["architect"])
        self.check(req, base)
        for changes in (dict(topic_id="other"), dict(issue_ids=["unknown"]), dict(target_roles=["product"]),
                        dict(action="ASK_USER"), dict(action="SUBMIT_REVIEW", target_roles=[])):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(req, dict(base, **changes))

    def test_scribe_validates_shared_schema_unique_ids_and_exact_provenance(self):
        base = dict(text="生成共识稿。", source_seqs=[7], content=SPEC, source_map=frozen()["source_map"],
                    agreements=[], alternatives=[], disagreements=[], assumptions=[], risks=[])
        req = task("scribe")
        self.check(req, base)
        for changes in (dict(content={}), dict(content=dict(SPEC, background=" ")),
                        dict(content=dict(SPEC, requirements=SPEC["requirements"] * 2)),
                        dict(source_map={}), dict(source_map={"FR-01": [7], "FR-02": [12]})):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(req, dict(base, **changes))

    def test_review_requires_frozen_current_draft_and_reconciles_previous_findings(self):
        for draft in (None, dict(frozen(), input_version=1), dict(frozen(), session_id="other"),
                      dict(frozen(), source_map={"FR-01": [99], "FR-02": [7]})):
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                task("reviewer", draft=draft)
        req = task("reviewer", draft=frozen(), issues=[issue()])
        closed = dict(issue(), description="已检查两个并发响应。", status="closed", source_seqs=[7])
        self.check(req, review(findings=[closed]))
        with self.assertRaisesRegex(RoleRuleError, "findings/0/source_seqs.*99"):
            self.check(req, review(findings=[dict(closed, source_seqs=[99])]))
        with self.assertRaisesRegex(RoleRuleError, "previously tracked issue"):
            self.check(task("reviewer", draft=frozen()), review(findings=[closed]))
        for output in (review(draft_id="d1"), review(), review(findings=[dict(closed, status="open")]),
                       review(findings=[dict(closed, target_item="FR-01")]),
                       review(findings=[dict(closed, severity="advisory")]),
                       review(findings=[dict(closed, closure_condition="自行降低关闭标准")]),
                       review(findings=[dict(closed, target_item="FR-99")]),
                       review(findings=[dict(closed, source_seqs=[])]),
                       review(verdict="REVISE", findings=[closed]),
                       review(verdict="NEEDS_INPUT", findings=[dict(closed, status="open")])):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.check(req, output)

    def test_new_user_input_can_update_old_parent_but_removed_ids_need_a_change_record(self):
        parent = dict(frozen(), input_version=1)
        req = task("scribe", draft=parent)
        content = dict(SPEC, requirements=[SPEC["requirements"][0]])
        output = dict(text="将已有约束合并到首条需求。", source_seqs=[7], content=content,
                      source_map={"FR-01": [7]}, agreements=[], alternatives=[], disagreements=[], assumptions=[], risks=[])
        with self.assertRaisesRegex(RoleRuleError, "removed ID"):
            self.check(req, output)
        changes = [dict(previous_id="FR-02", replacement_ids=["FR-01"], reason="用户要求合并预约约束。")]
        self.check(req, dict(output, requirement_changes=changes))
        with self.assertRaises(ValueError):
            task("host", draft=parent)

    def test_nonpassing_review_with_a_new_issue_can_be_revised_or_sent_to_user(self):
        req = task("reviewer", draft=frozen())
        finding = dict(issue(), status="open", source_seqs=[7])
        self.check(req, review(verdict="REVISE", findings=[finding]))
        self.check(req, review(verdict="NEEDS_INPUT", findings=[finding], questions=["请确认冲突时的行为。"]))
        self.check(req, review(findings=[dict(finding, severity="advisory")]))

    def test_rule_diagnostics_omit_model_supplied_identifiers(self):
        req = task("reviewer", draft=frozen())
        finding = dict(issue(), target_item="arbitrary provider text", status="open", source_seqs=[7])
        with self.assertRaises(RoleRuleError) as error:
            self.check(req, review(verdict="REVISE", findings=[finding]))
        self.assertNotIn(finding["target_item"], str(error.exception))

    def test_rejects_duplicate_keys_and_manual_role_override(self):
        with self.assertRaises(ValueError):
            validate_output(task("product"), '{"text":"a","text":"b","basis":"assumption","source_seqs":[]}')
        with self.assertRaises(ValueError):
            self.check(task("product"), dict(role="reviewer", text="通过", basis="assumption", source_seqs=[]))
