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

    def test_current_and_legacy_role_contracts_and_scoped_prompts(self):
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

    def test_mismatched_role_task_and_unassigned_evidence_are_rejected(self):
        for role in OUTPUT_MODELS:
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, "task kind"):
                task(role, kind="unknown")
        with self.assertRaisesRegex(ValueError, "assigned to the responding role"):
            task("research", kind="response", issues=[issue()])
        with self.assertRaisesRegex(ValueError, "two and eight"):
            task("architect", kind="stance", candidate_options=[{"option_id": "A", "title": "方案 A"}])
        with self.assertRaisesRegex(RoleRuleError, "fields must match"):
            self.check(task("architect", kind="statement"), {
                "text": "方案比较", "basis": "assumption", "source_seqs": [],
                "stances": [{"option_id": "A", "position": "conditional", "reason": "需验证"}],
            })

    def test_review_retains_complete_user_facing_paragraphs(self):
        prose = (
            "这版可以继续推进。首版只做一次计算两个数的网页版本，手机和电脑都能完成基本操作。"
            "这个范围已经明确，先把计算结果和出错提示做到一致。\n\n"
            "交付前请补齐桌面端的验收条件。明确用 Tab 键会依次经过哪些控件，"
            "并记下窗口缩到多窄时，按钮和结果仍然不会重叠。还需要写出计算结果的数值上限，"
            "并说明输入刚好达到上限和超过上限时，用户分别会看到什么。"
        )
        self.assertGreater(len(prose), 160)
        result = self.check(task("reviewer", draft=frozen()), review(plain_summary=prose))
        self.assertEqual(result.plain_summary, prose)

    def test_specialist_statement_ignores_extra_annotations_and_validates_evidence(self):
        req = task("architect", kind="statement")
        payload = {"text": "并发请求应共用同一个事务约束。", "source_seqs": [7], "basis": "evidence",
                   "recommendations": ["额外输出"], "owner_role": "host"}
        result = self.check(req, payload).model_dump()
        self.assertEqual(result["source_seqs"], [7])
        self.assertNotIn("recommendations", result)
        self.assertNotIn("owner_role", result)
        with self.assertRaisesRegex(RoleRuleError, "source_seqs"):
            self.check(req, dict(payload, source_seqs=[999]))
        with self.assertRaisesRegex(RoleRuleError, "assigned task kind"):
            self.check(req, dict(payload, responses=[{"issue_id": "I-1"}]))

    def test_specialist_tasks_use_only_the_assigned_structured_field(self):
        candidates = [{"option_id": key, "title": f"方案 {key}"} for key in ("A", "B")]
        stances = [dict(option_id=key, position="conditional", reason="需复核边界", source_seqs=[7]) for key in ("A", "B")]
        responses = [dict(issue_id="I-2", treatment="检查写入状态", verification="比较记录数", source_seqs=[7])]
        base = dict(text="检查并发", basis="evidence")
        agents = ("product", "research", "architect", "designer", "market")
        for role in agents:
            for kind, required, unrelated, items in (
                ("stance", "stances", "responses", stances), ("response", "responses", "stances", responses),
            ):
                with self.subTest(role=role, kind=kind):
                    req = task(role, kind=kind, issues=[dict(issue(), owner_role=role if kind == "response" else "scribe")],
                               candidate_options=candidates if kind == "stance" else [])
                    schema = json.loads(build_messages(req)[0]["content"].splitlines()[-1])
                    self.assertIn(required, schema["required"])
                    self.assertNotIn(unrelated, schema["properties"])
                    result = self.check(req, {**base, required: items, unrelated: [{"source_seqs": [999]}]}).model_dump()
                    self.assertEqual(result["source_seqs"], [7])
                    self.assertNotIn(unrelated, result)
                    with self.assertRaisesRegex(ValueError, required):
                        self.check(req, {**base, "source_seqs": [7], unrelated: items})
                    with self.assertRaisesRegex(RoleRuleError, required):
                        self.check(req, {**base, "source_seqs": [7], required: [dict(items[0], source_seqs=[999])] + items[1:]})

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

    def test_review_assigns_new_findings_to_current_specialists_and_keeps_historical_owners(self):
        req = task("reviewer", draft=frozen())
        current = dict(issue(), owner_role="designer", status="open", source_seqs=[7])
        self.check(req, review(verdict="REVISE", findings=[current]))
        for retired in ("research", "market", "scribe"):
            with self.subTest(role=retired), self.assertRaisesRegex(RoleRuleError, "owner_role"):
                self.check(req, review(verdict="REVISE", findings=[dict(current, owner_role=retired)]))
        legacy_issue = dict(issue(), owner_role="research", source_seqs=[7])
        legacy = task("reviewer", draft=frozen(), issues=[legacy_issue])
        checked = self.check(legacy, review(findings=[dict(legacy_issue, status="closed")]))
        self.assertEqual(checked.findings[0].owner_role, "research")

    def test_critic_can_conclude_no_new_issue_with_evidence(self):
        req = task()
        report = dict(text="已核对并发约束，本轮无新增异议。", source_seqs=[7], no_new_issues=True)
        self.assertTrue(self.check(req, report).no_new_issues)
        for changes in (dict(source_seqs=[]), dict(source_seqs=[17]), dict(target_roles=["architect"]),
                        dict(no_new_issues=False)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(req, {**report, **changes})

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

    def test_missing_source_field_recovers_only_from_role_appropriate_evidence(self):
        for role in ("product", "research", "architect", "designer", "market"):
            with self.subTest(role=role):
                opinion = {"text": "待验证的假设。", "basis": "assumption"}
                self.assertEqual(self.check(task(role), opinion).source_seqs, [])
                with self.assertRaises(ValueError):
                    self.check(task(role), {**opinion, "basis": "evidence"})
                self.assertEqual(self.check(task(role), {"text": "依据消息 #7。", "basis": "evidence"}).source_seqs, [7])

        host = dict(text="需要确认范围。", action="ASK_USER", topic_id="booking",
                    target_roles=[], issue_ids=[], questions=["首版范围是什么？"])
        self.assertEqual(self.check(task("host"), host).source_seqs, [])
        critic = dict(text="针对消息 7，如何验证？", target_roles=["architect"], verification="并发测试")
        self.assertEqual(self.check(task("critic"), critic).source_seqs, [7])
        for text in ("第 7 轮需要验证。", "针对消息 7 和消息 99。"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.check(task("critic"), {**critic, "text": text})
        for explicit in ([], [99], ["7"]):
            with self.subTest(explicit=explicit), self.assertRaises(ValueError):
                self.check(task("critic"), {**critic, "source_seqs": explicit})

        scribe = dict(text="规格整理完毕。", content=SPEC, source_map=frozen()["source_map"],
                      agreements=[], alternatives=[], disagreements=[], assumptions=[], risks=[])
        self.assertEqual(self.check(task("scribe"), scribe).source_seqs, [7])
        finding = dict(issue(), description="依据消息 #7，仍需验证。", status="open")
        evaluation = review(verdict="REVISE", findings=[finding])
        evaluation.pop("source_seqs")
        req = task("reviewer", draft=frozen())
        validated = self.check(req, evaluation)
        self.assertEqual(validated.source_seqs, [7])
        self.assertEqual(validated.findings[0].source_seqs, [7])
        with self.assertRaises(ValueError):
            self.check(req, {**evaluation, "findings": [{**finding, "description": "仍需验证。"}]})

    def test_scribe_preserves_long_technical_constraints_split_at_sentence_boundaries(self):
        first = "允许已验证的内部客户端访问受控接口，访问记录需保存供管理员复核。" * 2
        second = " 客户端缓存仅保留非敏感字段，且每次重新登录后清除上一会话的数据。" * 2
        combined = first + second
        req = task("scribe")
        base = dict(text="生成规格。", source_seqs=[7], source_map=frozen()["source_map"],
                    agreements=[], alternatives=[], disagreements=[], assumptions=[], risks=[])
        self.assertIn("每条最多 120 个字符", build_messages(req)[0]["content"])
        result = self.check(req, dict(base, content=dict(SPEC, technical_constraints=["启用访问审计。", combined])))
        parts = result.content["technical_constraints"]
        self.assertEqual(parts[0], "启用访问审计。")
        self.assertEqual("".join(parts[1:]), combined)
        self.assertTrue(all(len(part) <= 120 for part in parts))

        for constraints in (["甲" * 121], ["启用访问审计。"] * 29 + [combined]):
            with self.subTest(size=len(constraints)), self.assertRaisesRegex(
                RoleRuleError, r"technical_constraints.*maxLength \(actual \d+, limit 120\)",
            ) as failure:
                self.check(req, dict(base, content=dict(SPEC, technical_constraints=constraints)))
            self.assertNotIn("甲", str(failure.exception))

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
                       review(findings=[dict(closed, source_seqs=[])]),
                       review(verdict="REVISE", findings=[closed]),
                       review(verdict="NEEDS_INPUT", findings=[dict(closed, status="open")])):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.check(req, output)

    def test_review_uses_original_criteria_for_historical_findings(self):
        original = issue()
        req = task("reviewer", draft=frozen(), issues=[original])
        original_fields = {key: original[key] for key in ("target_item", "severity", "closure_condition")}
        assessment = dict(issue_id=original["issue_id"], owner_role="architect", description="复核结果及依据",
                          status="closed", source_seqs=[7])
        rewritten = dict(assessment, closure_condition="自行降低关闭标准", severity="advisory", target_item="FR-99")
        for finding in (assessment, rewritten):
            with self.subTest(finding=finding):
                result = self.check(req, review(findings=[finding])).findings[0]
                self.assertEqual(result.model_dump(include=set(original_fields)), original_fields)
                self.assertEqual((result.status, result.description, result.source_seqs), ("closed", assessment["description"], [7]))
        with self.assertRaisesRegex(RoleRuleError, "PASS cannot contain an open blocking finding"):
            self.check(req, review(findings=[dict(rewritten, status="open")]))
        new_finding = dict(assessment, issue_id="I-3", status="open")
        with self.assertRaisesRegex(ValueError, "closure_condition"):
            self.check(req, review(findings=[assessment, new_finding]))

    def test_review_can_reassess_prior_target_removed_from_the_current_draft(self):
        original = dict(issue(), target_item="FR-99")
        req = task("reviewer", draft=frozen(), issues=[original])
        finding = dict(original, description="已复核移除后的影响。", status="closed", source_seqs=[7])
        self.assertEqual(self.check(req, review(findings=[finding])).findings[0].target_item, "FR-99")
        with self.assertRaisesRegex(RoleRuleError, "unknown draft item"):
            self.check(req, review(findings=[finding, dict(finding, issue_id="I-3", status="open")]))

    def test_split_review_preserves_original_metadata_and_only_closes_registered_issues(self):
        req = task("reviewer", kind="review", draft=frozen(), issues=[issue()])
        result = review()
        del result["findings"]
        prior = dict(issue_id="I-2", status="closed", description="两次提交结果可以核对。", source_seqs=[7])
        new = dict(issue(), issue_id="I-3", severity="advisory", source_seqs=[7])
        output = self.check(req, dict(result, new_findings=[new], reassessments=[prior]))
        self.assertEqual([(finding.issue_id, finding.status) for finding in output.findings], [("I-3", "open"), ("I-2", "closed")])
        self.assertEqual(output.findings[1].closure_condition, issue()["closure_condition"])
        self.assertEqual(output.findings[1].owner_role, issue()["owner_role"])
        schema = json.loads(build_messages(req)[0]["content"].splitlines()[-1])
        self.assertNotIn("status", schema["$defs"]["NewFinding"]["properties"])
        self.assertEqual(schema["$defs"]["Reassessment"]["properties"]["issue_id"]["enum"], ["I-2"])
        with self.assertRaisesRegex(RoleRuleError, "must match a tracked issue"):
            self.check(req, dict(result, new_findings=[new], reassessments=[dict(prior, issue_id="unknown")]))
        with self.assertRaisesRegex(RoleRuleError, "already tracked"):
            self.check(req, dict(result, new_findings=[dict(new, issue_id="I-2")], reassessments=[prior]))
        with self.assertRaisesRegex(RoleRuleError, "all previous issues"):
            self.check(req, dict(result, new_findings=[new], reassessments=[]))

    def test_scribe_rejects_an_oversize_draft_before_send_to_review(self):
        template = SPEC["requirements"][0]
        items = [{**template, "id": f"FR-{index:03d}",
                  "acceptance_criteria": [f"场景 {item}：" + "中" * 1900 for item in range(20)]}
                 for index in range(1, 12)]
        output = dict(text="准备送审", source_seqs=[7], agreements=[], alternatives=[], disagreements=[], assumptions=[], risks=[],
                      content=dict(SPEC, requirements=items), source_map={item["id"]: [7] for item in items})
        with self.assertRaisesRegex(RoleRuleError, "delivery preflight: EXPORT_TOO_LARGE"):
            self.check(task("scribe", kind="draft"), output)

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

    def test_scribe_filters_orphan_changes_but_requires_real_removals(self):
        base = dict(text="整合规格。", source_seqs=[7], agreements=[], alternatives=[], disagreements=[], assumptions=[], risks=[])
        orphan = dict(previous_id="FR-99", replacement_ids=["FR-01"], reason="误引用了更早的草稿。")
        with_parent = {**base, "content": SPEC, "source_map": frozen()["source_map"], "requirement_changes": [orphan]}
        integration = task("scribe", kind="integration", draft=frozen())
        summary = self.check(integration, dict(with_parent, source_map={"wrong-id": [99]})).model_dump(exclude_none=True)
        self.assertEqual(summary, base)
        schema = json.loads(build_messages(integration)[0]["content"].splitlines()[-1])
        self.assertTrue({"content", "source_map", "requirement_changes"}.isdisjoint(schema["properties"]))
        self.assertEqual(self.check(task("scribe", kind="draft", draft=frozen()), with_parent).requirement_changes, [])
        rebuilt = task("scribe", kind="draft", previous_requirement_ids=["FR-01", "FR-02"])
        self.assertIsNone(rebuilt.draft)
        self.assertIn('["FR-01", "FR-02"]', build_messages(rebuilt)[0]["content"])
        trimmed = {**with_parent, "content": dict(SPEC, requirements=SPEC["requirements"][:1]), "source_map": {"FR-01": [7]}}
        self.assertEqual(self.check(task("scribe", kind="integration"), trimmed).model_dump(exclude_none=True), base)
        with self.assertRaisesRegex(RoleRuleError, "Missing previous IDs .*FR-02"):
            self.check(rebuilt, trimmed)
        actual = dict(previous_id="FR-02", replacement_ids=["FR-01"], reason="已合并到首条需求。")
        self.assertEqual([item.previous_id for item in self.check(
            rebuilt, {**trimmed, "requirement_changes": [orphan, actual]},
        ).requirement_changes], ["FR-02"])
        with self.assertRaisesRegex(RoleRuleError, "unknown replacement requirement"):
            self.check(rebuilt, {**trimmed, "requirement_changes": [{**actual, "replacement_ids": ["FR-99"]}]})

    def test_review_cannot_close_a_finding_with_an_unrelated_visible_reference(self):
        prior = {**issue(), "source_seqs": [7]}
        req = task("reviewer", draft=frozen(), issues=[prior], messages=[message(7), message(8)])
        closed = {**issue(), "description": "已经核对。", "status": "closed", "source_seqs": [8]}
        with self.assertRaisesRegex(RoleRuleError, "evidence for the issue being closed"):
            self.check(req, review(findings=[closed]))
        self.check(req, review(findings=[{**closed, "source_seqs": [7]}]))

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
