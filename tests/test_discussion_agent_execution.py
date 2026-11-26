import os
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
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
from backend.discussions.codec import dump_session, load_session
from backend.discussions.repository import LeaseLost
from backend.roles.runner import RoleRunner
from discussion_orchestration import (
    DiscussionSession, Issue, IssueStatus, Message, MessageKind, Phase, Role, RoundKind,
    SessionStatus, Severity, SpeakerTask, TaskKind, WaitingFor,
)
from discussion_orchestration.models import IssueResponse
from backend.roles.contracts import RoleRequest
from backend.roles.provider import Completion, DeepSeekProvider, ProviderError
from backend.roles.simulation import SimulationProvider


class DiscussionAgentExecutionTest(unittest.TestCase):
    def test_role_memory_recovers_checkpoints_and_issue_evidence_at_dispatch_boundary(self):
        session = DiscussionSession("session", "project", input_version=3, round_no=4,
                                    topic_id="T1", status=SessionStatus.DISCUSSING,
                                    waiting_for=WaitingFor.ROLE_RESULTS)
        session.messages = [
            Message(1, Role.USER, MessageKind.USER_INPUT, "首版只覆盖总部", 1, 0),
            Message(2, Role.USER, MessageKind.USER_INPUT, "目标用户是行政人员", 1, 1),
            Message(3, Role.USER, MessageKind.USER_INPUT, "目标用户是研发人员", 2, 2, replaces_answer_seq=2),
            Message(4, Role.PRODUCT, MessageKind.STATEMENT, "沿用行政人员流程", 2, 2, "T1"),
            Message(5, Role.RECORDER, MessageKind.INTEGRATION, "统一预约校验规则", 3, 3, "T1",
                    agreements=("统一预约校验规则",)),
            Message(6, Role.ARCHITECT, MessageKind.STATEMENT, "以事务边界保证一致性", 3, 3, "T1"),
            Message(7, Role.CHALLENGER, MessageKind.CHALLENGE, "事务失败时仍要释放名额", 3, 3, "T1"),
            Message(8, Role.RECORDER, MessageKind.INTEGRATION, "另一个议题的结论", 3, 3, "T2"),
            Message(9, Role.USER, MessageKind.USER_INPUT, "私人备注", 3, 4, substantive=False),
            Message(10, Role.HOST, MessageKind.DECISION, "评分后采纳方案 A", 3, 3, "T1", item_refs=("A", "B")),
        ]
        session.messages.extend(Message(seq, Role.PRODUCT, MessageKind.STATEMENT, "详" * 2000, 3, 4, "T1")
                                for seq in range(11, 50))
        session.issues["I1"] = Issue("I1", "T1", Role.CHALLENGER, "核对名额回滚", Severity.BLOCKING,
                                     source_seqs=(7,), assignee=Role.ARCHITECT, input_version=3)
        session.tasks["respond"] = SpeakerTask(
            "respond", session.session_id, Role.ARCHITECT, TaskKind.RESPONSE, Phase.RESPONSES,
            RoundKind.DIRECTED, 4, "T1", session.snapshot_ref, 3, tuple(range(11, 50)), ("I1",),
        )
        expected, = AgentExecution()._jobs(session)
        session.messages.append(Message(50, Role.DESIGNER, MessageKind.RESPONSE, "并行角色稍后提交", 3, 4, "T1"))
        recovered, = AgentExecution()._jobs(load_session(dump_session(session)))
        self.assertEqual(recovered.request, expected.request)
        messages = recovered.request.messages
        seqs = {message.seq for message in messages}
        self.assertTrue({1, 3, 5, 6, 7, 10, 49} <= seqs)
        self.assertTrue({2, 4, 8, 9, 50}.isdisjoint(seqs))
        self.assertEqual(recovered.request.consensus, ["统一预约校验规则"])
        self.assertEqual(recovered.request.issues[0].source_seqs, [7])
        self.assertLessEqual(sum(len(message.text) for message in messages if message.seq not in {1, 3, 7}), 16000)

    def test_integration_remembers_old_response_and_its_verification(self):
        session = DiscussionSession("session", "project", round_no=3, topic_id="T1",
                                    status=SessionStatus.DISCUSSING, waiting_for=WaitingFor.ROLE_RESULTS)
        session.messages = [
            Message(1, Role.USER, MessageKind.USER_INPUT, "预约会议室", 1, 0),
            Message(2, Role.USER, MessageKind.DECISION, "补上预约冲突的提示", 1, 1, "T1"),
            Message(3, Role.ARCHITECT, MessageKind.RESPONSE, "把冲突返回给发起人", 1, 2, "T1"),
            Message(4, Role.RECORDER, MessageKind.INTEGRATION, "接受已有预约优先", 1, 2, "T1"),
        ]
        session.messages.extend(Message(seq, Role.PRODUCT, MessageKind.STATEMENT, "其他讨论", 1, 3, "T1")
                                for seq in range(5, 45))
        session.issues["I1"] = Issue(
            "I1", "T1", Role.USER, "冲突提示", Severity.BLOCKING, source_seqs=(2,),
            assignee=Role.ARCHITECT, status=IssueStatus.ANSWERED, response_seq=3,
            response=IssueResponse("I1", "返回冲突状态", "同时提交两次预约并检查第二次收到提示", (2,)),
        )
        session.tasks["integrate"] = SpeakerTask(
            "integrate", session.session_id, Role.RECORDER, TaskKind.INTEGRATION, Phase.INTEGRATION,
            RoundKind.REVISION, 3, "T1", session.snapshot_ref, 1, tuple(range(5, 45)),
        )
        job, = AgentExecution()._jobs(session)
        issue, = job.request.issues
        self.assertEqual(issue.verification, "同时提交两次预约并检查第二次收到提示")
        self.assertEqual(issue.source_seqs, [2, 3])
        seqs = {message.seq for message in job.request.messages}
        self.assertTrue({1, 2, 3, 4, 44} <= seqs)
        self.assertNotIn(5, seqs)

    def advance_reviewed_revisions(self, client, path, *, max_returns=20):
        state = client.get(path).json()
        for _ in range(max_returns):
            if state["status"] != "awaiting_confirmation" or state["active_draft"]["review_verdict"] != "revise":
                return state
            draft_id = state["active_draft"]["draft_id"]
            self.assertFalse(state["pending_tasks"])
            returned = client.post(f"{path}/actions", headers={"Idempotency-Key": str(uuid4())}, json={
                "type": "require_changes", "payload": {"draft_id": draft_id},
                "expected_state_version": state["state_version"],
            })
            self.assertTrue(returned.json()["decision"]["ok"], returned.text)
            state = client.get(path).json()
            self.assertNotEqual(state["active_draft"]["draft_id"], draft_id, state)
        self.fail("模拟会话没有在预期次数内收敛")

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
            output["source_seqs"] = [max(m.seq for m in request.messages) + 1]
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
                self.assertIn("source_seqs outside the task snapshot", diagnostic)
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
                self.assertTrue(challenges[0]["verification"])
                self.assertTrue(all(message["basis"] for message in messages if message["kind"] == "statement"))
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
                note = client.post(f"{path}/messages", headers={"Idempotency-Key": str(uuid4())}, json={
                    "text": "只备注这条讨论", "reply_to_seq": clarification["message_seq"],
                    "substantive": False, "expected_state_version": resumed["state_version"],
                })
                self.assertTrue(note.json()["decision"]["ok"], note.text)
                resumed = client.get(path).json()
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

    def test_identical_pending_agent_task_stops_without_repeating_provider_call(self):
        complete = SimulationProvider.complete
        calls = []

        async def track_call(provider, request, messages):
            calls.append(request.logical_task_id)
            return await complete(provider, request, messages)

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", track_call), \
                patch.object(AgentExecution, "_apply", return_value=False):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={
                    "idea": "Book a room",
                }, headers={"Idempotency-Key": str(uuid4())})
                self.assertEqual(created.status_code, 201, created.text)
                state = client.get(f"/api/v1/discussions/{created.json()['id']}").json()
                self.assertEqual(state["waiting_for"], "host_agenda")
                self.assertEqual(len(calls), 1)
                self.assertEqual(state["participants"][0]["error_code"], "AGENT_NO_PROGRESS")
                self.assertEqual(service.repo.pending_runs("simulation"), [])

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

    def test_value_divergence_goes_to_user_instead_of_peer_debate(self):
        complete = SimulationProvider.complete

        async def value_critic(provider, request, messages):
            response = await complete(provider, request, messages)
            if request.role == "critic":
                output = json.loads(response.content)
                output["divergence"] = "value"
                output["user_question"] = "首版优先开放全部预约时段，还是限制高峰时段？"
                return Completion(json.dumps(output), response.usage)
            return response

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", value_critic):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={
                    "idea": "名称：会议室预约。用户：行政人员。目标：预约会议室。",
                }, headers={"Idempotency-Key": str(uuid4())})
                self.assertEqual(created.status_code, 201, created.text)
                state = client.get(f"/api/v1/discussions/{created.json()['id']}").json()

                self.assertEqual(state["status"], "clarifying", state)
                self.assertEqual(state["waiting_for"], "user_input")
                self.assertTrue(state["clarification"]["questions"])
                self.assertTrue(any(issue.get("kind") == "value" for issue in state["open_issues"]))
                self.assertEqual(state["clarification"]["questions"], ["首版优先开放全部预约时段，还是限制高峰时段？"])
                # 价值分歧不派发点名回应：没有 response 消息，也不进入定向补充轮。
                messages = client.get(f"/api/v1/discussions/{created.json()['id']}/messages").json()["items"]
                self.assertFalse([m for m in messages if m["kind"] == "response"])

    def test_tradeoff_divergence_triggers_scoring_before_draft(self):
        complete = SimulationProvider.complete
        scoring_requests = []

        async def tradeoff_critic(provider, request, messages):
            if request.kind == "scoring":
                scoring_requests.append(request)
            response = await complete(provider, request, messages)
            if request.role == "critic":
                output = json.loads(response.content)
                output["divergence"] = "tradeoff"
                return Completion(json.dumps(output), response.usage)
            return response

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", tradeoff_critic):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={
                    "idea": "名称：会议室预约。用户：行政人员。目标：预约会议室。",
                }, headers={"Idempotency-Key": str(uuid4())})
                self.assertEqual(created.status_code, 201, created.text)
                session_id = created.json()["id"]
                state = self.advance_reviewed_revisions(client, f"/api/v1/discussions/{session_id}")

                self.assertEqual(state["status"], "awaiting_confirmation", state)
                self.assertIsNotNone(state["scorecard"], state)
                self.assertEqual(state["scorecard"]["chosen_option_id"], "A")
                self.assertEqual(set(state["scorecard"]["stances"]), {"product", "research", "architect", "designer", "business"})
                self.assertTrue(all(
                    {position["option_id"] for position in stances} == {"A", "B"}
                    for stances in state["scorecard"]["stances"].values()
                ))
                self.assertEqual([option["option_id"] for option in state["scorecard"]["options"]], ["A", "B"])
                self.assertEqual(state["active_draft"]["chosen_option_id"], "A")
                self.assertEqual(state["active_draft"]["scorecard_id"], state["scorecard"]["scorecard_id"])
                first = state["drafts"][0]
                history = client.get(f"/api/v1/discussions/{session_id}/drafts/{first['draft_id']}")
                self.assertEqual(history.status_code, 200, history.text)
                historical = history.json()
                self.assertTrue(historical["content"]["requirements"])
                self.assertEqual(historical["review_verdict"], "revise")
                self.assertEqual(historical["scorecard_id"], historical["scorecard"]["scorecard_id"])
                self.assertNotEqual(historical["scorecard_id"], state["active_draft"]["scorecard_id"])
                self.assertTrue(historical["scorecard"]["stances"])
                self.assertEqual(state["active_draft"]["parent_id"], first["draft_id"])

                self.assertGreaterEqual(len(scoring_requests), 2)
                self.assertNotEqual(scoring_requests[0].issues[0].issue_id, scoring_requests[-1].issues[0].issue_id)
                for request in scoring_requests:
                    self.assertEqual([issue.kind for issue in request.issues], ["tradeoff"])
                    self.assertTrue(request.issues[0].source_seqs)


                confirmed = client.post(f"/api/v1/discussions/{session_id}/confirm", json={
                    "draft_id": state["active_draft"]["draft_id"],
                    "input_version": state["input_version"],
                    "expected_state_version": state["state_version"],
                }, headers={"Idempotency-Key": str(uuid4())})
                self.assertTrue(confirmed.json()["decision"]["ok"], confirmed.text)
                # 评分卡只用于决策与展示，不进入 FlowStudio 导入正文。
                exported = json.loads(client.get(
                    f"/api/v1/discussions/{session_id}/export").content.decode("utf-8"))
                self.assertNotIn("scorecard_id", exported)
                self.assertNotIn("chosen_option_id", exported)

    def test_structured_objections_route_by_owner_and_grounded_stances_change_ranking(self):
        complete = SimulationProvider.complete
        requests = []
        score_attempts = 0
        review_attempts = 0

        async def structured(provider, request, messages):
            nonlocal score_attempts, review_attempts
            requests.append(request)
            response = await complete(provider, request, messages)
            output = json.loads(response.content)
            evidence = next((m.seq for m in request.messages if m.role == "product"), request.messages[0].seq)
            if request.kind == "challenge":
                output = {"text": "并发处理与失败反馈需要分别验证。", "objections": [
                    {"description": "并发时可能重复提交", "owner_role": "architect", "source_seqs": [evidence],
                     "verification": "并发请求只产生一条记录"},
                    {"description": "失败时缺少可见提示", "owner_role": "designer", "source_seqs": [evidence],
                     "verification": "失败时可识别重试入口"},
                    {"description": "首版选择同步还是异步", "owner_role": "product", "source_seqs": [evidence],
                     "verification": "比较两种方案的风险", "divergence": "tradeoff"},
                ]}
            if request.kind == "scoring":
                output["scorecard"]["options"] = [
                    {"option_id": key, "title": title, "scores": {"user_value": 4, "feasibility": 4,
                     "cost": 4, "differentiation": 4, "risk": risk}}
                    for key, title, risk in [("A", "同步提交", 4), ("B", "异步登记", 4)]
                ]
                score_attempts += 1
                if score_attempts == 1:
                    output["scorecard"]["options"][1]["option_id"] = "A"
            if request.kind == "review":
                review_attempts += 1
                output["new_findings"] = [
                    {key: value for key, value in finding.items() if key != "status"}
                    for finding in output.pop("findings")
                ]
                output["reassessments"] = []
                if review_attempts == 1:
                    output["reassessments"] = [{"issue_id": "untracked", "status": "closed",
                                                "description": "尚未登记", "source_seqs": [evidence]}]
            if request.kind == "stance":
                output["responses"] = [{"issue_id": request.issues[0].issue_id, "treatment": "额外回应",
                                        "verification": "额外验证", "source_seqs": [evidence]}]
                if request.role in {"architect", "designer"}:
                    output["stances"][0].update(position="oppose", score_dimension="risk", source_seqs=[evidence],
                                                 reason="同步提交的失败分支仍需控制重复记录和错误提示。")
            if request.kind in {"agenda", "stance", "review"}:
                output["plain_summary"] = "先说清楚用户会看到什么。"
            return Completion(json.dumps(output), response.usage)

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", structured):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={
                    "idea": "名称：预约。用户：行政人员。目标：预约会议室。",
                }, headers={"Idempotency-Key": str(uuid4())})
                self.assertEqual(created.status_code, 201, created.text)
                path = f"/api/v1/discussions/{created.json()['id']}"
                state = client.get(path).json()
                self.assertEqual(state["status"], "awaiting_confirmation", state)
                self.assertEqual(score_attempts, 2)
                self.assertEqual(review_attempts, 2)
                messages = client.get(f"{path}/messages?limit=100").json()["items"]
                for kind in ("agenda", "stance", "review"):
                    selected = [message for message in messages if message["kind"] == kind]
                    self.assertTrue(selected)
                    self.assertTrue(all(message["plain_summary"] == "先说清楚用户会看到什么。" for message in selected))
                    self.assertTrue(all(message["text"] != message["plain_summary"] for message in selected))
                responses = {request.role: request.issues for request in requests if request.kind == "response"}
                self.assertEqual(set(responses), {"architect", "designer"})
                self.assertEqual(responses["architect"][0].closure_condition, "并发请求只产生一条记录")
                self.assertEqual(responses["designer"][0].closure_condition, "失败时可识别重试入口")
                review_inputs = [request for request in requests if request.kind == "draft"]
                self.assertEqual({issue.owner_role for issue in review_inputs[-1].issues}, {"architect", "designer"})
                stances = [request for request in requests if request.kind == "stance"]
                self.assertTrue(all([issue.kind for issue in request.issues] == ["tradeoff"] for request in stances))
                self.assertTrue(stances)
                card = state["scorecard"]
                self.assertEqual(card["chosen_option_id"], "B")
                self.assertEqual(state["active_draft"]["chosen_option_id"], "B")
                self.assertEqual({option["option_id"]: option["scores"]["risk"] for option in card["options"]}, {"A": 3, "B": 4})
                self.assertIn("A 风险可控性 4→3", card["rationale"])

    def test_workers_claim_once_and_stale_worker_cannot_write(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "discussion.sqlite3"
            first, second = build_service(db), build_service(db)
            first.agent_mode = second.agent_mode = "simulation"
            _, initial = first.create(str(uuid4()), "Book a room")
            sid = initial["id"]
            with patch("backend.discussions.repository.time", return_value=100), ThreadPoolExecutor(2) as pool:
                contenders = ("first", "second")
                claims = list(pool.map(lambda pair: pair[0].repo.claim_run(sid, "simulation", pair[1], 5),
                                       zip((first, second), contenders)))
                self.assertCountEqual(claims, [1, None])
                owner = contenders[claims.index(1)]
                self.assertEqual(first.repo.pending_runs("real"), [])
                self.assertEqual(first.repo.pending_runs("simulation"), [])
            with patch("backend.discussions.repository.time", return_value=106):
                self.assertEqual(second.repo.claim_run(sid, "simulation", "replacement", 5), 1)
                session, meta = first.repo.load_state(sid)
                with self.assertRaises(LeaseLost):
                    first.repo.save_after_decision(session, meta["state_version"], lease_owner=owner)
                first.repo.release_run(sid, owner, 1, finished=True)
                second.repo.renew_run(sid, "replacement", 5)
                second.act(sid, action="run_agents", payload={}, expected_state_version=meta["state_version"],
                           idempotency_key=None, request_hash="", scope="")
                second.repo.release_run(sid, "replacement", 1, finished=True)
                self.assertEqual(second.repo.pending_runs("simulation"), [sid])

    def test_concurrent_idempotent_create_enqueues_one_session(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "discussion.sqlite3"
            services = (build_service(db), build_service(db))

            def create(service):
                service.agent_mode = "simulation"
                return service.create("project", "Book a room", idempotency_key="same-key",
                                      request_hash="same-request", scope="create")

            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(create, services))
            self.assertEqual(results[0], results[1])
            self.assertEqual(services[0].repo.pending_runs("simulation"), [results[0][1]["id"]])

    def test_server_start_resumes_persisted_execution_without_browser_action(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}):
            db = Path(directory) / "discussion.sqlite3"
            before_restart = build_service(db)
            before_restart.agent_mode = "simulation"
            _, initial = before_restart.create(str(uuid4()), "名称：会议室预约。用户：行政人员。目标：预约会议室。")
            sid = initial["id"]
            with patch("backend.discussions.repository.time", return_value=100):
                self.assertEqual(before_restart.repo.claim_run(sid, "simulation", "stopped-worker", 1), 1)
                before_restart.repo.record_attempt(
                    session_id=sid, logical_task_id=f"{sid}:agent:agenda:v1", attempt_no=1, role="host",
                    task_kind="agenda", snapshot_ref="snap-v1", input_version=1, status="RUNNING",
                    lease_owner="stopped-worker",
                )
            resumed = build_service(db)
            self.assertEqual(resumed.repo.list_attempts(sid)[0]["status"], "RUNNING")
            with TestClient(create_app(resumed, with_agents=True)) as client:
                path = f"/api/v1/discussions/{sid}"
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    state = client.get(path).json()
                    if state["status"] == "awaiting_confirmation":
                        break
                    sleep(0.05)
                self.assertEqual(state["status"], "awaiting_confirmation", state)
                self.assertEqual(state["active_draft"]["review_verdict"], "revise")
                self.assertFalse(state["pending_tasks"])
                # Show that the background recovery also stopped at the human gate.
                self.assertEqual(client.get(path).json()["last_seq"], state["last_seq"])
                attempts = resumed.repo.list_attempts(sid)
                host = [a for a in attempts if a["role"] == "host"]
                self.assertEqual([a["status"] for a in host], ["FAILED", "SUCCEEDED"])
                self.assertEqual(host[0]["error"]["code"], "PROCESS_INTERRUPTED")
                self.assertEqual(state["usage"]["calls"], len(attempts) - 1)
                self.assertGreater(state["usage"]["elapsed_seconds"], 0)
                self.assertGreater(state["usage"]["by_task"]["review"]["calls"], 0)
                self.assertEqual(resumed.repo.pending_runs("simulation"), [])

    def test_fixed_ideas_preserve_user_scope_and_improve_acceptance_criteria(self):
        cases = [
            ("会议室预约", "行政人员", "预约不得重叠", "首版仅覆盖总部", "只生成一个有效结果"),
            ("补货申请", "库房员工", "提交缺货申请", "仅支持内部试用", "返回结果与用例中的预期值一致"),
        ]
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}):
            service = build_service(Path(directory) / "quality.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                for name, audience, goal, scope, observable in cases:
                    with self.subTest(case=name):
                        created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={
                            "idea": f"名称：{name}。用户：{audience}。目标：{goal}。约束：{scope}。",
                        }, headers={"Idempotency-Key": str(uuid4())})
                        self.assertEqual(created.status_code, 201, created.text)
                        sid = created.json()["id"]
                        state = self.advance_reviewed_revisions(client, f"/api/v1/discussions/{sid}")
                        self.assertEqual(state["status"], "awaiting_confirmation", state)
                        session, _ = service.repo.load_state(sid)
                        before = session.drafts[0].content["requirements"][0]
                        final = session.current_draft.content
                        after, = final["requirements"]
                        self.assertEqual(final["target_users"], [audience])
                        self.assertEqual(final["development_constraints"], [scope])
                        self.assertEqual(final["technical_constraints"], [])
                        self.assertEqual((after["id"], after["text"]), (before["id"], goal))
                        self.assertFalse(any(observable in criterion for criterion in before["acceptance_criteria"]))
                        self.assertTrue(any(observable in criterion for criterion in after["acceptance_criteria"]))
                        self.assertTrue(after["source_seqs"])

    def test_revisions_switch_bounded_execution_batches_automatically(self):
        complete = SimulationProvider.complete
        runners = []
        review_requests = []

        def track_runner(*args, **kwargs):
            runner = RoleRunner(*args, **kwargs, call_limit=4)
            runners.append(runner)
            return runner

        async def extended_revision(provider, request, messages):
            response = await complete(provider, request, messages)
            output = json.loads(response.content)
            if request.kind == "draft" and request.draft:
                output["content"]["requirements"][0]["acceptance_criteria"].append(
                    f"并发上限为 {request.draft.revision + 1} 时，返回明确的受理结果。"
                )
            if request.role == "reviewer":
                review_requests.append(request)
            if request.role == "reviewer" and request.draft.revision < 18:
                output["verdict"] = "REVISE"
                for finding in output["findings"]:
                    finding["status"] = "open"
            return Completion(json.dumps(output), response.usage)

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", extended_revision), \
                patch("backend.discussions.agent_execution.RoleRunner", side_effect=track_runner):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={
                    "idea": "名称：会议室预约。用户：行政人员。目标：预约会议室。",
                }, headers={"Idempotency-Key": str(uuid4())})
                self.assertEqual(created.status_code, 201, created.text)
                session_id = created.json()["id"]
                state = self.advance_reviewed_revisions(client, f"/api/v1/discussions/{session_id}")
                self.assertEqual(state["status"], "awaiting_confirmation", state)
                self.assertEqual(state["active_draft"]["version"], 18)
                session, _ = service.repo.load_state(session_id)
                self.assertGreater(session.dispatch_units, 64)
                final_review = review_requests[-1]
                visible = {message.seq for message in final_review.messages}
                self.assertLess(len(visible), len(session.messages))
                self.assertTrue(all(seq in visible for seqs in final_review.draft.source_map.values() for seq in seqs))
                self.assertTrue(all(seq in visible for issue in final_review.issues for seq in issue.source_seqs))
                self.assertNotIn(next(message.seq for message in session.messages if message.kind.value == "agenda"), visible)
                self.assertGreater(len(runners), 1)
                attempts = service.repo.list_attempts(session_id)
                self.assertEqual(sum(runner.calls_used for runner in runners), len(attempts))
                self.assertGreater(len(runners), state["active_draft"]["version"])
                self.assertTrue(all(0 < runner.calls_used <= 4 for runner in runners))
                self.assertTrue(all(attempt["status"] == "SUCCEEDED" for attempt in attempts))
                self.assertEqual(len({attempt["logical_task_id"] for attempt in attempts}), len(attempts))

    def test_repeated_unchanged_revision_requests_clarification(self):
        complete = SimulationProvider.complete

        async def unresolved_review(provider, request, messages):
            response = await complete(provider, request, messages)
            if request.role != "reviewer":
                return response
            output = json.loads(response.content)
            output["verdict"] = "REVISE"
            for finding in output["findings"]:
                finding["status"] = "open"
            return Completion(json.dumps(output), response.usage)

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", unresolved_review):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={
                    "idea": "名称：会议室预约。用户：行政人员。目标：预约会议室。",
                }, headers={"Idempotency-Key": str(uuid4())})
                self.assertEqual(created.status_code, 201, created.text)
                state = self.advance_reviewed_revisions(client, f"/api/v1/discussions/{created.json()['id']}")
                self.assertEqual(state["status"], "clarifying", state)
                self.assertEqual(state["waiting_for"], "user_input")
                self.assertEqual(state["active_draft"]["version"], 3)
                self.assertEqual(state["reviews"][-1]["verdict"], "revise")
                self.assertTrue(state["clarification"]["questions"])

    def test_legacy_quota_pause_resumes_without_more_quota(self):
        complete = SimulationProvider.complete

        async def failed_review(provider, request, messages):
            if request.role == "reviewer":
                raise ProviderError("revision_rounds_exceeded")
            return await complete(provider, request, messages)

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}):
            service = build_service(Path(directory) / "discussion.sqlite3")
            with TestClient(create_app(service, with_agents=True)) as client:
                with patch.object(SimulationProvider, "complete", failed_review):
                    created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={
                        "idea": "名称：会议室预约。用户：行政人员。目标：预约会议室。",
                    }, headers={"Idempotency-Key": str(uuid4())})
                self.assertEqual(created.status_code, 201, created.text)
                session_id = created.json()["id"]
                path = f"/api/v1/discussions/{session_id}"
                session, meta = service.repo.load_state(session_id)
                self.assertEqual(session.status, SessionStatus.REVIEWING)
                service.engine.pause(session, "修订额度已耗尽，等待用户补充证据或授权追加额度")
                session._codec_extra = {"limits": {"max_revision_rounds": 0}}
                saved = service.repo.save_after_decision(session, meta["state_version"])
                resumed = client.post(f"{path}/actions", headers={"Idempotency-Key": str(uuid4())}, json={
                    "type": "resume", "expected_state_version": saved["state_version"],
                })
                self.assertTrue(resumed.json()["decision"]["ok"], resumed.text)
                state = self.advance_reviewed_revisions(client, path)
                self.assertEqual(state["status"], "awaiting_confirmation", state)
                self.assertEqual(state["active_draft"]["version"], 2)

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
                initial = client.get(path).json()
                self.assertEqual(initial["status"], "awaiting_confirmation", initial)
                self.assertEqual(initial["active_draft"]["review_verdict"], "revise")
                self.assertFalse(initial["pending_tasks"])
                stale = client.post(f"{path}/actions", headers=headers(), json={
                    "type": "require_changes", "payload": {"draft_id": "previous-draft"},
                    "expected_state_version": initial["state_version"],
                })
                self.assertEqual(stale.status_code, 409, stale.text)
                self.assertEqual(client.get(path).json()["state_version"], initial["state_version"])
                state = self.advance_reviewed_revisions(client, path)
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
