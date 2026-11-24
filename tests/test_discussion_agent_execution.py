import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from backend.discussions.routes import build_service, create_app
from backend.roles.contracts import RoleRequest
from backend.roles.provider import Completion, DeepSeekProvider, ProviderError
from backend.roles.simulation import SimulationProvider


class DiscussionAgentExecutionTest(unittest.TestCase):
    def test_failed_review_preserves_diagnostic_and_retries_only_the_frozen_draft(self):
        complete = SimulationProvider.complete
        received = []
        roles = []
        diagnostic = "literal_error at findings/0/severity"

        async def invalid_then_valid(provider, request, messages):
            roles.append(request.role)
            response = await complete(provider, request, messages)
            if request.role != "reviewer":
                return response
            received.append((request.draft.id, messages))
            output = json.loads(response.content)
            if len(received) <= 2:
                output["findings"][0]["severity"] = "private-model-value"
            return Completion(json.dumps(output), {"total_tokens": 11})

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", invalid_then_valid):
            service = build_service(Path(directory) / "test.sqlite3")
            headers = lambda: {"Idempotency-Key": str(uuid4())}
            with TestClient(create_app(service, with_agents=True)) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={"idea": "Book rooms"},
                                      headers=headers())
                self.assertEqual(created.status_code, 201, created.text)
                session_id = created.json()["id"]
                path = f"/api/v1/discussions/{session_id}"
                state = client.get(path).json()
                self.assertEqual(state["waiting_for"], "review")
                reviewer = next(p for p in state["participants"] if p["role"] == "reviewer")
                self.assertEqual(reviewer.get("diagnostic"), diagnostic)
                attempts = [r for r in service.repo.list_attempts(session_id) if r["role"] == "reviewer"]
                self.assertEqual([a["attempt_no"] for a in attempts], [1, 2])
                for attempt in attempts:
                    self.assertEqual(attempt["error"], {"code": "invalid_output", "diagnostic": diagnostic})
                    self.assertEqual(attempt["usage"]["total_tokens"], 11)
                    self.assertNotIn("private-model-value", json.dumps(attempt))
                before = len(roles)
                retry = client.post(f"{path}/actions", json={"type": "run_agents",
                                    "expected_state_version": state["state_version"]}, headers=headers())
                self.assertTrue(retry.json()["decision"]["ok"], retry.text)
                after = client.get(path).json()
                self.assertEqual(after["waiting_for"], "user_input")
                self.assertEqual(after["reviews"][-1]["verdict"], "needs_input")
                self.assertEqual(roles[before:], ["reviewer"])
                self.assertEqual([draft for draft, _ in received], [state["active_draft"]["draft_id"]] * 3)
                for _, messages in received[1:]:
                    self.assertIn(diagnostic, str(messages))
                    self.assertNotIn("private-model-value", str(messages))

    def test_create_from_idea_automatically_persists_first_exchange(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                creation = client.post(
                    f"/api/v1/projects/{uuid4()}/discussions",
                    json={"idea": "Build an internal tool for booking meeting rooms"},
                    headers={"Idempotency-Key": str(uuid4())},
                )
                self.assertEqual(creation.status_code, 201, creation.text)
                path = f"/api/v1/discussions/{creation.json()['id']}"
                state = client.get(path).json()
                messages = client.get(f"{path}/messages").json()["items"]
                self.assertEqual(state["waiting_for"], "user_input")
                self.assertEqual(state["reviews"][-1]["verdict"], "needs_input")
                self.assertEqual([m["role"] for m in messages if m["kind"] == "agenda"], ["host"])
                self.assertCountEqual([m["role"] for m in messages if m["kind"] == "statement"],
                                      ["product", "research", "architect", "designer", "business"])
                challenges = [m for m in messages if m["kind"] == "challenge"]
                self.assertEqual(len(challenges), 1)
                self.assertEqual(challenges[0]["role"], "challenger")
                self.assertTrue(challenges[0]["source_seqs"])
                self.assertFalse(state["open_issues"])
                self.assertEqual({p["status"] for p in state["participants"] if p["role"] != "user"}, {"succeeded"})

                retry = client.post(f"{path}/actions", headers={"Idempotency-Key": str(uuid4())},
                                    json={"type": "run_agents", "expected_state_version": state["state_version"]})
                self.assertTrue(retry.json()["decision"]["ok"], retry.text)
                self.assertEqual(client.get(f"{path}/messages").json()["items"], messages)

    def test_retry_runs_only_unfinished_tasks(self):
        complete = SimulationProvider.complete

        async def fail_research(provider, request, messages):
            if request.role == "research":
                raise ProviderError("test_failure")
            return await complete(provider, request, messages)

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                with patch.object(SimulationProvider, "complete", fail_research):
                    creation = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={"idea": "Book rooms"},
                                           headers={"Idempotency-Key": str(uuid4())})
                self.assertEqual(creation.status_code, 201, creation.text)
                path = f"/api/v1/discussions/{creation.json()['id']}"
                state = client.get(path).json()
                self.assertEqual([task["role"] for task in state["pending_tasks"]], ["research"])
                self.assertIn("test_failure", [person.get("error_code") for person in state["participants"]])
                retry = client.post(f"{path}/actions", headers={"Idempotency-Key": str(uuid4())},
                                    json={"type": "run_agents", "expected_state_version": state["state_version"]})
                self.assertTrue(retry.json()["decision"]["ok"], retry.text)
                messages = client.get(f"{path}/messages").json()["items"]
                self.assertCountEqual([m["role"] for m in messages if m["kind"] == "statement"],
                                      ["product", "research", "architect", "designer", "business"])
                self.assertEqual(client.get(path).json()["waiting_for"], "user_input")

    def test_real_provider_runs_revision_until_user_signoff(self):
        calls = []

        async def model(request):
            self.assertEqual(request.url.host, "api.deepseek.com")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "deepseek-flash")
            role_request = RoleRequest.model_validate_json(body["messages"][-1]["content"])
            calls.append(role_request.role)
            output = await SimulationProvider().complete(role_request, body["messages"])
            content = output.content
            if role_request.role == "reviewer" and calls.count("reviewer") == 1:
                invalid = json.loads(content)
                invalid["draft_id"] = "different-draft"
                content = json.dumps(invalid)
            elif role_request.role == "reviewer" and calls.count("reviewer") == 2:
                self.assertIn("review must identify the frozen draft", str(body["messages"]))
            return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})

        def provider(**kwargs):
            return DeepSeekProvider(**kwargs, transport=httpx.MockTransport(model))

        env = {"DISCUSSION_AGENT_MODE": "real", "DEEPSEEK_API_KEY": "test-key", "DISCUSSION_MODEL": "deepseek-flash"}
        with TemporaryDirectory() as directory, patch.dict(os.environ, env), \
                patch("backend.discussions.agent_execution.DeepSeekProvider", provider):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                headers = lambda: {"Idempotency-Key": str(uuid4())}
                creation = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={
                    "idea": "名称：会议室预约。用户：行政人员。目标：预约会议室。",
                }, headers=headers())
                self.assertEqual(creation.status_code, 201, creation.text)
                path = f"/api/v1/discussions/{creation.json()['id']}"
                state = client.get(path).json()
                self.assertEqual(state["status"], "awaiting_confirmation", state)
                self.assertEqual([review["verdict"] for review in state["reviews"]], ["revise", "pass"])
                self.assertEqual(calls.count("reviewer"), 3)
                self.assertGreater(calls.count("critic"), 1)
                self.assertTrue(all(attempt["usage"]["total_tokens"] == 10 for attempt in service.repo.list_attempts(state["id"])))
                confirmation = client.post(f"{path}/confirm", headers=headers(), json={
                    "draft_id": state["active_draft"]["draft_id"], "input_version": state["input_version"],
                    "expected_state_version": state["state_version"],
                })
                self.assertTrue(confirmation.json()["decision"]["ok"], confirmation.text)
