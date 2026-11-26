"""Concurrency regressions using isolated SQLite files and a simulated provider."""

import asyncio
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from backend.discussions.agent_execution import AgentExecution
from backend.discussions.errors import ApiError
from backend.discussions.routes import build_service
from backend.roles.simulation import SimulationProvider

IDEA = "名称：会议室预约。用户：行政人员。目标：预约会议室。"


class ConcurrentWriteTests(TestCase):
    def test_identical_concurrent_writes_reuse_one_response(self):
        with TemporaryDirectory() as temp:
            service = build_service(Path(temp) / "writes.sqlite")
            _, state = service.create("project", IDEA)
            session_id = state["id"]
            workers = 12
            barrier = Barrier(workers, timeout=10)
            save = service.repo.save_after_decision

            def synchronized_save(*args, **kwargs):
                barrier.wait()
                return save(*args, **kwargs)

            def submit(_):
                return service.post_message(
                    session_id, text="重复提交的备注", reply_to_seq=None, substantive=False,
                    expected_state_version=state["state_version"], idempotency_key="shared",
                    request_hash="same-body", scope="messages",
                )

            with patch.object(service.repo, "save_after_decision", synchronized_save), ThreadPoolExecutor(workers) as pool:
                results = list(pool.map(submit, range(workers)))
            self.assertTrue(all(result == results[0] for result in results))
            history = service.messages(session_id, 0, 20)["items"]
            self.assertEqual(len(history), 2)
            self.assertFalse(history[-1]["substantive"])
            with self.assertRaises(ApiError) as conflict:
                service.post_message(
                    session_id, text="不同正文", reply_to_seq=None, substantive=False,
                    expected_state_version=state["state_version"], idempotency_key="shared",
                    request_hash="different-body", scope="messages",
                )
            self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")


class AgentStressTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 0.5

    async def test_two_workers_share_jobs_and_bound_active_sessions(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}):
            service = build_service(Path(temp) / "workers.sqlite")
            service.agent_mode = "simulation"
            sessions = [service.create("project", IDEA)[1]["id"] for _ in range(12)]
            runners = [AgentExecution(), AgentExecution()]
            calls = Counter()
            running = [0, 0]
            peaks = [0, 0]
            complete = SimulationProvider.complete

            async def tracked_complete(provider, request, messages):
                calls[request.logical_task_id] += 1
                await asyncio.sleep(0.001)
                return await complete(provider, request, messages)

            def track_drive(worker, index):
                drive = worker._drive

                async def tracked_drive(*args, **kwargs):
                    running[index] += 1
                    peaks[index] = max(peaks[index], running[index])
                    try:
                        await drive(*args, **kwargs)
                    finally:
                        running[index] -= 1

                worker._drive = tracked_drive

            for index, runner in enumerate(runners):
                track_drive(runner, index)
            with patch.object(SimulationProvider, "complete", tracked_complete):
                await asyncio.wait_for(asyncio.gather(*(
                    runner.run(service, session_id)
                    for session_id in sessions for runner in runners
                )), timeout=40)

            self.assertGreaterEqual(max(peaks), 2)
            self.assertLessEqual(max(peaks), AgentExecution.MAX_ACTIVE_SESSIONS)
            self.assertGreater(sum(calls.values()), 100)
            self.assertTrue(all(count == 1 for count in calls.values()), calls)
            self.assertEqual(service.repo.pending_runs("simulation"), [])
            for session_id in sessions:
                state = service.view(session_id)
                self.assertEqual(state["status"], "awaiting_confirmation", state)
                self.assertEqual(state["active_draft"]["review_verdict"], "revise")

    async def test_delete_during_call_revokes_lease_and_model_result(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}):
            service = build_service(Path(temp) / "delete.sqlite")
            service.agent_mode = "simulation"
            _, state = service.create("project", IDEA)
            session_id = state["id"]
            started, finish = asyncio.Event(), asyncio.Event()
            complete = SimulationProvider.complete

            async def outstanding_call(provider, request, messages):
                started.set()
                await finish.wait()
                return await complete(provider, request, messages)

            with patch.object(SimulationProvider, "complete", outstanding_call):
                task = asyncio.create_task(AgentExecution().run(service, session_id))
                await asyncio.wait_for(started.wait(), timeout=3)
                service.delete(session_id)
                finish.set()
                await asyncio.wait_for(task, timeout=3)

            self.assertEqual(service.repo.pending_runs("simulation"), [])
            self.assertEqual([attempt["status"] for attempt in service.repo.list_attempts(session_id)], ["CANCELLED"])
            with self.assertRaises(ApiError) as missing:
                service.view(session_id)
            self.assertEqual(missing.exception.status, 404)

    async def test_notes_are_visible_in_chat_and_excluded_from_all_role_inputs(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}):
            service = build_service(Path(temp) / "notes.sqlite")
            service.agent_mode = "simulation"
            _, state = service.create("project", IDEA)
            session_id = state["id"]
            note = "NOTE_ONLY_OLD_SCOPE"
            service.post_message(
                session_id, text=note, reply_to_seq=None, substantive=False,
                expected_state_version=state["state_version"], idempotency_key=None, request_hash="", scope="",
            )
            observed = []
            complete = SimulationProvider.complete

            async def inspect_input(provider, request, messages):
                observed.append(request)
                return await complete(provider, request, messages)

            with patch.object(SimulationProvider, "complete", inspect_input):
                await AgentExecution().run(service, session_id)

            self.assertEqual({request.role for request in observed},
                             {"host", "product", "architect", "designer", "critic", "reviewer"})
            self.assertTrue(all(note not in request.model_dump_json() for request in observed))
            self.assertTrue(any(message["text"] == note and not message["substantive"]
                                for message in service.messages(session_id, 0, 200)["items"]))
