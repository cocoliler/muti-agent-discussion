import asyncio
import json
import unittest

from backend.roles.contracts import RoleRequest
from backend.roles.provider import Completion, ProviderError
from backend.roles.runner import RoleRunner


def request(task="task-1", **changes):
    fields = dict(role="product", logical_task_id=task, session_id="s",
                  input_version=1, topic_id="scope", user_input="设计校园失物招领系统")
    return RoleRequest.model_validate({**fields, **changes})


class Fake:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = 0

    async def complete(self, req, messages):
        self.calls += 1
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoring_retries_missing_dimensions_before_submission(self):
        scores = dict(user_value=4, feasibility=3, cost=3, differentiation=2, risk=4)
        card = dict(text="比较方案", source_seqs=[], action="SCORE", topic_id="scope", scorecard={"options": [
            dict(option_id="A", title="方案 A", scores={key: value for key, value in scores.items() if key != "risk"}),
            dict(option_id="B", title="方案 B", scores=scores),
        ]})
        bad = Completion(json.dumps(card))
        card["scorecard"]["options"][0]["scores"] = scores
        provider = Fake(bad, Completion(json.dumps(card)))
        result = await RoleRunner("s", provider, mode="real").run(request(role="host", kind="scoring"))
        self.assertIsNone(result.error)
        self.assertEqual(result.attempts[0].diagnostic, "missing at scorecard/options/0/scores/risk")
        self.assertEqual(provider.calls, 2)

    async def test_research_can_correct_a_missing_or_out_of_scope_source(self):
        bad = {"text": "建议核实", "basis": "evidence", "source_seqs": [17]}
        good = {"text": "建议核实", "basis": "assumption", "source_seqs": []}
        provider = Fake(Completion(json.dumps(bad)), Completion(json.dumps(good)))
        result = await RoleRunner("s", provider, mode="real").run(request(role="research"))
        self.assertIsNone(result.error)
        self.assertEqual(len(result.attempts), 2)
        self.assertIn("17", result.attempts[0].diagnostic)

    async def test_invalid_source_is_diagnosable_with_usage(self):
        output = {"text": "test", "source_seqs": [999], "basis": "evidence"}
        runner = RoleRunner("s", Fake(Completion(json.dumps(output), {"total_tokens": 7})), mode="real", call_limit=1)
        result = await runner.run(request())
        self.assertEqual(result.error, "invalid_output")
        self.assertIn("source_seqs", result.attempts[0].diagnostic)
        self.assertEqual(result.attempts[0].usage["total_tokens"], 7)

    async def test_retry_budget_and_usage_on_malformed_output(self):
        provider = Fake(ProviderError("timeout", True), Completion("{}", {"total_tokens": 20}))
        runner = RoleRunner("s", provider, mode="real", call_limit=2)
        result = await runner.run(request())
        self.assertEqual(result.error, "invalid_output")
        self.assertEqual([a.error for a in result.attempts], ["timeout", "invalid_output"])
        self.assertEqual([a.attempt_no for a in result.attempts], [1, 2])
        self.assertEqual(result.attempts[1].usage, {"total_tokens": 20})
        self.assertIsNone(result.attempts[0].usage)
        self.assertEqual((await runner.run(request("task-2"))).error, "call_limit")
        self.assertEqual(provider.calls, 2)

    async def test_parallel_limit_counts_before_dispatch(self):
        class Slow:
            calls = 0
            async def complete(self, req, messages):
                self.calls += 1
                await asyncio.sleep(0.03)
                raise ProviderError("auth")
        provider = Slow()
        runner = RoleRunner("s", provider, mode="real", call_limit=1)
        results = await asyncio.gather(runner.run(request("a")), runner.run(request("b")))
        self.assertCountEqual([r.error for r in results], ["call_limit", "auth"])
        self.assertEqual(provider.calls, 1)

    async def test_wall_clock_timeout_and_retry_limit(self):
        class Slow:
            async def complete(self, req, messages):
                await asyncio.sleep(10)
        runner = RoleRunner("s", Slow(), mode="real", timeout_seconds=0.01)
        result = await runner.run(request())
        self.assertEqual(result.error, "timeout")
        self.assertEqual(len(result.attempts), 2)

    async def test_no_cross_session_dispatch(self):
        provider = Fake()
        runner = RoleRunner("s", provider, mode="simulation")
        with self.assertRaises(ValueError):
            await runner.run(request(session_id="different"))
        self.assertEqual(provider.calls, 0)

    async def test_resume_reserved_retry_after_authorized_budget_increase(self):
        ok = {"text": "确认范围", "source_seqs": [], "basis": "assumption"}
        provider = Fake(ProviderError("server", True), Completion(json.dumps(ok), {"total_tokens": 12}))
        runner = RoleRunner("s", provider, mode="real", call_limit=1)
        self.assertEqual((await runner.run(request())).error, "call_limit")
        self.assertEqual((await runner.run(request())).error, "call_limit")
        runner.extend_budget(1)
        self.assertEqual((await runner.run(request(user_input="另一个快照"))).error, "task_snapshot_conflict")
        result = await runner.run(request())
        self.assertIsNone(result.error)
        self.assertEqual([a.attempt_no for a in result.attempts], [1, 2])
        self.assertEqual([a.error for a in result.attempts], ["server", None])
        self.assertEqual(runner.calls_used, 2)
