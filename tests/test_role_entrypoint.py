"""从协调器接口验证 REAL 模式的传输、校验、重试和重复派发。"""

import asyncio
import json
from pathlib import Path
import unittest

import httpx

from backend.roles.contracts import RoleRequest
from backend.roles.provider import DeepSeekProvider
from backend.roles.runner import RoleRunner


def host_request():
    sample = Path(__file__).resolve().parents[1] / "backend/fixtures/role-host-input.json"
    return RoleRequest.model_validate_json(sample.read_bytes())


class EntryPointTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_adapter_retries_same_snapshot_and_deduplicates_success(self):
        snapshot = host_request()
        sent = []
        output = {"text": "请产品经理界定借用范围。", "source_seqs": [1], "action": "DISCUSS",
                  "topic_id": snapshot.topic_id, "target_roles": ["product"],
                  "issue_ids": [], "questions": []}

        def handle(request):
            sent.append(json.loads(request.content))
            if len(sent) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            })

        provider = DeepSeekProvider(api_key="local-test", model="test-model",
                                    transport=httpx.MockTransport(handle))
        runner = RoleRunner(snapshot.session_id, provider, mode="real", call_limit=2)
        result = await runner.run(snapshot)
        self.assertIsNone(result.error)
        self.assertEqual(result.model, "test-model")
        self.assertEqual(result.output["target_roles"], ["product"])
        self.assertEqual([a.error for a in result.attempts], ["server", None])
        self.assertEqual([a.status for a in result.attempts], ["failed", "succeeded"])
        self.assertEqual(result.attempts[1].usage["total_tokens"], 30)
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(await runner.run(snapshot), result)
        changed = snapshot.model_copy(update={"input_version": snapshot.input_version + 1})
        self.assertEqual((await runner.run(changed)).error, "task_snapshot_conflict")
        self.assertEqual(len(sent), 2)

    async def test_parallel_duplicate_and_cancellation(self):
        started = asyncio.Event()

        class WaitingProvider:
            async def complete(self, request, messages):
                started.set()
                await asyncio.Event().wait()

        req = host_request()
        runner = RoleRunner(req.session_id, WaitingProvider(), mode="real")
        pending = asyncio.create_task(runner.run(req))
        await started.wait()
        self.assertEqual((await runner.run(req)).error, "task_in_progress")
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        previous = await runner.run(req)
        self.assertEqual(previous.error, "cancelled")
        self.assertEqual(previous.attempts[0].status, "cancelled")
        self.assertEqual(len(previous.attempts), 1)
        self.assertEqual(runner.calls_used, 1)
