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
from backend.discussions.agent_execution import AgentExecution
from discussion_orchestration import SessionStatus, WaitingFor
from backend.roles.contracts import RoleRequest
from backend.roles.provider import Completion, DeepSeekProvider, ProviderError
from backend.roles.simulation import SimulationProvider


class DiscussionAgentExecutionTest(unittest.TestCase):
    def test_invalid_integration_retries_with_diagnostic_and_resumes_same_task(self):
        complete = SimulationProvider.complete
        prompts = []

        async def invalid_then_valid(provider, request, messages):
            response = await complete(provider, request, messages)
            if request.role != "scribe" or ":integration:" not in request.logical_task_id:
                return response
            prompts.append(messages)
            if len(prompts) > 2:
                return response
            output = json.loads(response.content)
            source_map = output["source_map"]
            source_map["private-model-value"] = source_map.pop(output["content"]["requirements"][0]["id"])
            return Completion(json.dumps(output), response.usage)

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", invalid_then_valid):
            service = build_service(Path(directory) / "test.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={"idea": "Book rooms"},
                                      headers={"Idempotency-Key": str(uuid4())})
                self.assertEqual(created.status_code, 201, created.text)
                session_id = created.json()["id"]
                path = f"/api/v1/discussions/{session_id}"
                state = client.get(path).json()
                self.assertEqual([task["kind"] for task in state["pending_tasks"]], ["integration"])
                attempts = [r for r in service.repo.list_attempts(session_id) if r["task_kind"] == "integration"]
                self.assertEqual([a["status"] for a in attempts], ["FAILED", "FAILED"])
                diagnostic = attempts[0]["error"]["diagnostic"]
                self.assertIn("Missing keys for content.requirements indices [0]", diagnostic)
                self.assertIn("extra source_map key indices", diagnostic)
                self.assertNotIn("private-model-value", diagnostic)
                retry = client.post(f"{path}/actions", headers={"Idempotency-Key": str(uuid4())}, json={
                    "type": "run_agents", "expected_state_version": state["state_version"],
                })
                self.assertTrue(retry.json()["decision"]["ok"], retry.text)
                self.assertEqual(client.get(path).json()["waiting_for"], "user_input")
                self.assertEqual(len(prompts), 3)
                for prompt in prompts[1:]:
                    self.assertEqual(prompt[-1], prompts[0][-1])
                    self.assertEqual(json.loads(prompt[1]["content"]), {"previous_validation_error": diagnostic})
                attempts = [r for r in service.repo.list_attempts(session_id) if r["task_kind"] == "integration"]
                self.assertEqual([a["status"] for a in attempts], ["FAILED", "FAILED", "SUCCEEDED"])

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
                clarification = state["clarification"]
                self.assertEqual(clarification["message_seq"], messages[-1]["seq"])
                self.assertEqual(messages[-1]["kind"], "clarification")
                self.assertEqual(len(clarification["questions"]), 1)
                self.assertIn("由谁使用", clarification["questions"][0])
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
                state = client.get(path).json()
                answer = client.post(f"{path}/messages", headers={"Idempotency-Key": str(uuid4())}, json={
                    "text": f"1. {clarification['questions'][0]}\n回答：行政人员",
                    "reply_to_seq": clarification["message_seq"], "substantive": True,
                    "input_version": state["input_version"], "expected_state_version": state["state_version"],
                    "clarification_answers": [{"question": clarification["questions"][0], "answer": "行政人员",
                                                "options": ["行政人员", "研发人员", "客服人员", "所有员工"]}],
                })
                self.assertTrue(answer.json()["decision"]["ok"], answer.text)
                resumed = client.get(path).json()
                self.assertEqual(resumed["input_version"], state["input_version"] + 1)
                session, _ = service._load(state["id"])
                second = session.drafts[1]
                first = session.drafts[0]
                self.assertEqual(second.parent_id, first.draft_id)
                # 旧数据库的 v2 没有 parent_id，重试也必须收到 v1 的结构化评审。
                for draft in session.drafts:
                    draft.current = draft is second
                second.parent_id = None
                session.status, session.waiting_for = SessionStatus.REVIEWING, WaitingFor.REVIEW
                job, = AgentExecution()._jobs(session)
                prior = {f.issue_id: list(f.source_seqs) for f in session.review_of(first.draft_id).findings}
                self.assertTrue(prior)
                self.assertTrue(all(prior.values()))
                self.assertEqual({i.issue_id: i.source_seqs for i in job.request.issues}, prior)
                self.assertTrue(all(set(i.source_seqs) <= {m.seq for m in job.request.messages}
                                    for i in job.request.issues))
                self.assertEqual(resumed["active_draft"]["content"]["target_users"], ["行政人员"])
                self.assertIsNone(resumed["clarification"])
                original = next(message for message in answer.json()["new_messages"] if message["role"] == "user")
                self.assertEqual(resumed["clarification_answers"][str(original["seq"])][0]["answer"], "行政人员")
                revision = {
                    "text": f"1. {clarification['questions'][0]}\n回答：研发人员",
                    "reply_to_seq": clarification["message_seq"], "substantive": True,
                    "input_version": resumed["input_version"], "expected_state_version": resumed["state_version"],
                    "replaces_answer_seq": original["seq"],
                    "clarification_answers": [{"question": clarification["questions"][0], "answer": "研发人员"}],
                }
                replacement = client.post(f"{path}/messages", headers={"Idempotency-Key": str(uuid4())}, json=revision)
                self.assertTrue(replacement.json()["decision"]["ok"], replacement.text)
                updated = client.get(path).json()
                self.assertEqual(updated["active_draft"]["content"]["target_users"], ["研发人员"])
                self.assertIn(original["seq"], updated["superseded_answer_seqs"])
                history = client.get(f"{path}/messages?limit=100").json()["items"]
                self.assertTrue(any(m["seq"] == original["seq"] and "行政人员" in m["text"] for m in history))
                stale = client.post(f"{path}/messages", headers={"Idempotency-Key": str(uuid4())}, json={
                    **revision, "input_version": updated["input_version"], "expected_state_version": updated["state_version"],
                })
                self.assertFalse(stale.json().get("decision", {}).get("ok", False), stale.text)

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
            elif role_request.role == "reviewer" and role_request.issues:
                revised = json.loads(content)
                revised["findings"][0].update(target_item="unknown", severity="advisory", closure_condition="Rewritten by the model")
                content = json.dumps(revised)
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
