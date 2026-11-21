import json
import unittest
from copy import deepcopy
from pathlib import Path

from backend.roles.contracts import RoleRequest, build_messages, validate_output
from backend.roles.provider import ProviderError
from backend.roles.simulation import SimulationProvider


FIXTURES = Path(__file__).resolve().parents[1] / "backend" / "fixtures"


class SimulationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.base = json.loads((FIXTURES / "role-host-input.json").read_text(encoding="utf-8"))
        self.provider = SimulationProvider()

    async def run_role(self, role, **overrides):
        request = RoleRequest.model_validate(dict(self.base, role=role, **overrides))
        result = await self.provider.complete(request, build_messages(request))
        self.assertEqual(result.usage["total_tokens"], 0)
        return validate_output(request, result.content).model_dump()

    def freeze(self, output, revision=1):
        return dict(id=f"candidate-{revision}", session_id=self.base["session_id"], input_version=1,
                    revision=revision, content=output["content"], source_map=output["source_map"])

    def prior_issues(self, review):
        return [{k: v for k, v in f.items() if k != "status"} for f in review["findings"]]

    async def test_fixture_and_five_specialists_respond_to_idea_deterministically(self):
        agenda = await self.run_role("host")
        self.assertEqual(agenda["action"], "DISCUSS")
        self.assertEqual(agenda, await self.run_role("host"))
        opinions = [await self.run_role(role) for role in agenda["target_roles"]]
        self.assertEqual(len({op["text"] for op in opinions}), 5)
        for op in opinions:
            self.assertIn("可借器材", op["text"])
            self.assertEqual(op["source_seqs"], [1])
        internal = await self.run_role("market", user_input="内部工具：档案整理")
        self.assertEqual(internal["basis"], "not_applicable")

    async def test_critic_references_selected_message_and_respects_allowed_roles(self):
        msg = dict(self.base["messages"][0], role="designer", seq=7, text="首次使用时显示空列表")
        challenge = await self.run_role("critic", messages=[msg], allowed_roles=["designer"])
        self.assertEqual(challenge["source_seqs"], [7])
        self.assertEqual(challenge["target_roles"], ["designer"])
        self.assertIn(msg["text"], challenge["text"])
        with self.assertRaisesRegex(ProviderError, "insufficient_context"):
            await self.run_role("critic", messages=[])

    async def test_old_candidate_is_immutable_and_review_depends_on_content(self):
        content = json.loads((FIXTURES / "meeting-discussion-spec.json").read_text(encoding="utf-8"))
        self.base["user_input"] = self.base["messages"][0]["text"] = "员工需要查询和预约会议室，预约时段不得重叠。"
        # The revision number alone never determines the outcome.
        frozen = self.freeze(dict(content=content, source_map={"FR-01": [1], "FR-02": [1]}), 10)
        good = await self.run_role("reviewer", draft=frozen)
        self.assertEqual(good["verdict"], "PASS")
        frozen["content"]["requirements"][1]["acceptance_criteria"] = ["能处理冲突。"]
        old = deepcopy(frozen)
        failed = await self.run_role("reviewer", draft=frozen)
        self.assertEqual(failed["verdict"], "REVISE")
        self.assertEqual([i["target_item"] for i in failed["findings"]], ["FR-02"])
        history = self.prior_issues(failed)
        update = await self.run_role("scribe", draft=frozen, issues=history)
        self.assertEqual(frozen, old)
        self.assertEqual(update["content"]["requirements"][0], content["requirements"][0])
        self.assertEqual(update["source_map"], frozen["source_map"])
        new = self.freeze(update, 11)
        checked = await self.run_role("reviewer", draft=new, issues=history)
        self.assertEqual(checked["verdict"], "PASS")
        self.assertEqual(checked["findings"][0]["status"], "closed")
        self.assertEqual(checked["findings"][0]["issue_id"], failed["findings"][0]["issue_id"])

    async def test_user_clarification_is_applied_only_with_evidence(self):
        self.base["user_input"] = self.base["messages"][0]["text"] = "做一个离线星图工具"
        draft = self.freeze(await self.run_role("scribe"))
        review = await self.run_role("reviewer", draft=draft)
        self.assertEqual(review["verdict"], "NEEDS_INPUT")
        self.assertTrue(review["questions"])
        history = self.prior_issues(review)
        unchanged = await self.run_role("scribe", draft=draft, issues=history)
        unresolved = await self.run_role("reviewer", draft=self.freeze(unchanged, 2), issues=history)
        self.assertEqual(unresolved["verdict"], "NEEDS_INPUT")
        messages = self.base["messages"] + [dict(self.base["messages"][0], seq=2, text="目标用户：天文社学生。")]
        fixed = await self.run_role("scribe", draft=draft, issues=history, messages=messages)
        self.assertEqual(fixed["content"]["target_users"], ["天文社学生"])
        rerun = await self.run_role("reviewer", draft=self.freeze(fixed, 3), issues=history, messages=messages)
        self.assertEqual(rerun["verdict"], "PASS")
        audience = next(f for f in rerun["findings"] if f["target_item"] == "target_users")
        self.assertIn(2, audience["source_seqs"])

    async def test_external_finding_needs_a_targeted_supported_treatment(self):
        draft = self.freeze(await self.run_role("scribe"))
        issue = dict(issue_id="EDGE", severity="blocking", target_item="FR-01", owner_role="designer",
                     closure_condition="写明损坏器材的处理方式。", description="缺少损坏分支。")
        extra = "当学生选择已损坏的器材时，页面显示维修状态并禁用借用按钮。"
        messages = self.base["messages"] + [dict(self.base["messages"][0], role="designer", seq=2, text=extra)]
        updated = await self.run_role("scribe", draft=draft, issues=[issue], messages=messages)
        self.assertEqual(updated["content"], draft["content"])
        issue.update(treatment=extra, source_seqs=[2])
        updated = await self.run_role("scribe", draft=draft, issues=[issue], messages=messages)
        self.assertIn(extra, updated["content"]["requirements"][0]["acceptance_criteria"])
        self.assertEqual(updated["source_map"]["FR-01"], [1, 2])
        review = await self.run_role("reviewer", draft=self.freeze(updated, 2), issues=[issue], messages=messages)
        self.assertEqual(review["verdict"], "PASS")
        self.assertEqual(review["findings"][0]["status"], "closed")

    async def test_review_traces_evidence_and_rechecks_new_sources(self):
        draft = self.freeze(await self.run_role("scribe"))
        unrelated = [dict(self.base["messages"][0], text="The night sky is clear.")]
        review = await self.run_role("reviewer", draft=draft, messages=unrelated)
        self.assertEqual(review["verdict"], "NEEDS_INPUT")
        history = self.prior_issues(review)
        self.assertEqual((await self.run_role("host", issues=history, messages=unrelated))["action"], "ASK_USER")
        supported = unrelated + [dict(self.base["messages"][0], seq=2)]
        update = await self.run_role("scribe", draft=draft, issues=history, messages=supported)
        self.assertIn(2, update["source_map"]["FR-01"])
        review = await self.run_role("reviewer", draft=self.freeze(update, 2), issues=history, messages=supported)
        self.assertEqual(review["verdict"], "PASS")

    async def test_cites_the_goal_and_consensus_instead_of_an_unrelated_latest_message(self):
        fact = "管理员能标记器材已归还"
        messages = self.base["messages"] + [
            dict(self.base["messages"][0], role="product", seq=2, text=f"范围已确认：{fact}。"),
            dict(self.base["messages"][0], seq=3, text="目标用户：学生、图书馆员。"),
        ]
        out = await self.run_role("scribe", messages=messages, consensus=[fact])
        self.assertEqual(out["source_map"], {"FR-01": [1], "FR-02": [2]})
        self.assertEqual(out["content"]["target_users"], ["学生", "图书馆员"])
