import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.discussions.routes import build_service, create_app
from discussion_orchestration import Issue, IssueStatus, Role, Severity


class DiscussionActivityTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = build_service(Path(self.directory.name) / "discussion.sqlite3")
        self.app = create_app(self.service)
        with TestClient(self.app) as client:
            response = client.post(f"/api/v1/projects/{uuid4()}/discussions",
                                   json={"idea": "会议室预约"}, headers={"Idempotency-Key": str(uuid4())})
        self.assertEqual(response.status_code, 201, response.text)
        self.session_id = response.json()["id"]
        self.path = f"/api/v1/discussions/{self.session_id}"

    def start(self, role):
        self.service.repo.record_attempt(
            session_id=self.session_id, logical_task_id=role, attempt_no=1, role=role,
            task_kind="statement", snapshot_ref="private-snapshot", input_version=1, status="RUNNING",
        )

    def test_parallel_attempts_are_paged_and_private_payloads_are_omitted(self):
        with TestClient(self.app) as client:
            before = self.service.activity_marker(self.session_id)
            self.start("architect")
            self.start("designer")
            running = self.service.activity_marker(self.session_id)
            self.assertNotEqual(before, running)
            page = client.get(f"{self.path}/activity?limit=1").json()
            self.assertEqual([(item["role"], item["status"]) for item in page["items"]], [("designer", "RUNNING")])
            older = client.get(f"{self.path}/activity", params={"before": page["next_cursor"]}).json()
            self.assertEqual([(item["role"], item["status"]) for item in older["items"]], [("architect", "RUNNING")])
            self.assertIsNone(older["next_cursor"])
            self.service.repo.finish_attempt(
                session_id=self.session_id, logical_task_id="designer", attempt_no=1, status="FAILED",
                error={"code": "invalid_output", "diagnostic": "private-diagnostic"}, output="private-model-output",
            )
            self.assertNotEqual(running, self.service.activity_marker(self.session_id))
            response = client.get(f"{self.path}/activity")
            self.assertEqual(response.status_code, 200)
            first = response.json()["items"][0]
            self.assertEqual((first["status"], first["error_code"]), ("FAILED", "invalid_output"))
            self.assertNotIn("private-", response.text)

    def test_stream_notifies_when_an_attempt_changes(self):
        async def check_stream():
            changes = []
            disconnect = asyncio.Event()
            responses = []
            request_started = False

            async def receive():
                nonlocal request_started
                if not request_started:
                    request_started = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await disconnect.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.start":
                    responses.append(message)
                if b"event: change" in message.get("body", b""):
                    changes.append(message["body"])
                    if len(changes) == 1:
                        await asyncio.to_thread(self.start, "architect")
                    elif len(changes) == 2:
                        disconnect.set()

            path = f"{self.path}/events"
            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                     "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
                     "root_path": "", "query_string": b"", "headers": []}
            await asyncio.wait_for(self.app(scope, receive, send), timeout=6)
            self.assertEqual(responses[0]["status"], 200)
            self.assertIn(b"text/event-stream", dict(responses[0]["headers"])[b"content-type"])
            self.assertEqual(changes, [b"event: change\ndata: {}\n\n"] * 2)

        asyncio.run(check_stream())

    def test_issue_lookup_preserves_sources_from_an_earlier_input_version(self):
        session, meta = self.service.repo.load_state(self.session_id)
        session.input_version = 2
        issue_id = "I-1-1-1"
        session.issues[issue_id] = Issue(
            issue_id, "T1", Role.CHALLENGER, "补齐键盘验收条件", Severity.MAJOR,
            source_seqs=(1,), status=IssueStatus.CLOSED, response_seq=1, closed_by_seq=1,
            input_version=1, closure_condition="用键盘完成一次计算",
        )
        self.service.repo.save_after_decision(session, meta["state_version"])
        with TestClient(self.app) as client:
            response = client.get(f"{self.path}/issues/{issue_id}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {
                "issue_id": issue_id, "summary": "补齐键盘验收条件", "status": "closed", "input_version": 1,
                "source_seqs": [1], "closure_condition": "用键盘完成一次计算", "response_seq": 1, "closed_by_seq": 1,
            })
            self.assertEqual(client.get(f"{self.path}/issues/I-9-9-9").status_code, 404)
