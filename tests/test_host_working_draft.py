import json
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.discussions.agent_execution import AgentExecution
from backend.discussions.routes import build_service, install
from backend.roles.provider import Completion
from backend.roles.simulation import SimulationProvider

IDEA = "名称：会议室预约。用户：行政人员。目标：预约不得冲突。约束：首版只覆盖总部。"


def headers():
    return {"Idempotency-Key": str(uuid4())}


class HostWorkingDraftTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "DISCUSSION_AGENT_MODE": "simulation", "DISCUSSION_INITIAL_DRAFT_MODE": "draft_first",
            "DISCUSSION_INITIAL_CONSTRAINTS": "[]",
        }))
        directory = self.enterContext(TemporaryDirectory())
        self.db = Path(directory) / "discussion.sqlite3"
        self.service = build_service(self.db)
        app = FastAPI()
        install(app, self.service, with_agents=True, with_export=True)
        self.client = self.enterContext(TestClient(app))

    def create(self, idea=IDEA):
        response = self.client.post(f"/api/v1/projects/{uuid4()}/discussions", json={"idea": idea}, headers=headers())
        self.assertEqual(response.status_code, 201, response.text)
        session_id = response.json()["id"]
        return session_id, f"/api/v1/discussions/{session_id}"

    def supplement(self, path, state, text, *, reply=None):
        response = self.client.post(f"{path}/messages", json={
            "text": text, "substantive": True, "reply_to_seq": reply,
            "input_version": state["input_version"], "expected_state_version": state["state_version"],
        }, headers=headers())
        response.raise_for_status()
        return self.client.get(path).json()

    def test_single_host_job_publishes_linked_modules_and_warning_before_statements(self):
        original = SimulationProvider.complete
        observed = []

        async def simulate(provider, request, messages):
            if request.kind == "statement":
                session, _ = self.service.repo.load_state(request.session_id)
                observed.append((session.initial_draft, session.current_draft))
            result = await original(provider, request, messages)
            if request.kind == "initial_outline":
                output = json.loads(result.content)
                output["content"]["open_questions"].append({
                    "question_id": "office-hours", "question": "会议室预订时段是否需要限制？",
                    "category": "important", "reason": "会影响工作时段之外的使用",
                })
                return Completion(json.dumps(output), result.usage)
            return result

        with patch.object(SimulationProvider, "complete", simulate):
            sid, path = self.create()
        state = self.client.get(path).json()
        self.assertEqual(state["status"], "awaiting_confirmation", state)
        draft = state["initial_draft"]
        self.assertEqual(draft["schema_version"], 2)
        body = draft["working_draft"]
        self.assertEqual((body["session_id"], body["input_version"], body["lifecycle"]), (sid, 1, "working"))
        self.assertTrue(body["snapshot_ref"])
        self.assertEqual(body["content"]["constraints"][0]["text"], "首版只覆盖总部")
        modules = body["content"]["modules"]
        self.assertGreaterEqual(len(modules), 3)
        self.assertEqual(modules[1]["depends_on"], [modules[0]["id"]])
        self.assertEqual(draft["quality_gate_result"]["status"], "PASS_WITH_WARNINGS")
        self.assertTrue(observed)
        self.assertTrue(all(stored.draft_id == draft["draft_id"] and frozen is None for stored, frozen in observed))
        metrics = self.client.get(f"{path}/draft-metrics").json()
        boundary = next(m for m in metrics["milestones"] if m["stage"] == "outline_committed")
        calls = [item for item in metrics["attempts"] if item["id"] <= boundary["attempt_high_water"]]
        self.assertEqual([item["task_kind"] for item in calls], ["initial_outline"])
        self.assertIsNone(calls[0]["prompt_tokens"])
        reread = build_service(self.db).working_draft(sid, draft["draft_id"])
        self.assertEqual(reread["working_draft"], body)
        self.assertEqual(self.client.get(f"{path}/drafts/{draft['draft_id']}").status_code, 404)
        denied = self.client.post(f"{path}/confirm", json={
            "draft_id": draft["draft_id"], "input_version": 1, "expected_state_version": state["state_version"],
        }, headers=headers())
        self.assertEqual(denied.json()["decision"]["code"], "STALE_DRAFT")

    def test_clarifies_then_rebuilds_for_new_input(self):
        sid, path = self.create("想做个工具")
        state = self.client.get(path).json()
        self.assertEqual((state["status"], state["waiting_for"], state["round_no"]), ("clarifying", "user_input", 0))
        self.assertIsNone(state["initial_draft"])
        self.assertEqual(state["clarification"]["questions"], ["首版要解决哪个业务问题？"])
        state = self.supplement(path, state, IDEA, reply=state["clarification"]["message_seq"])
        working = state["initial_draft"]["working_draft"]
        self.assertEqual(working["input_version"], 2)
        metrics = self.client.get(f"{path}/draft-metrics").json()
        self.assertEqual([turn["asked_count"] for turn in metrics["initial_clarifications"]], [1])
        self.assertEqual([row["input_version"] for row in metrics["attempts"] if row["task_kind"] == "initial_outline"],
                         [1, 2])

    def test_parallel_specialists_publish_auditable_revision_then_independent_review(self):
        original = SimulationProvider.complete
        requests = {}
        waiting = 0
        peak = 0
        gate = None
        observed_drafts = []
        old_jobs = {}

        async def simulate(provider, request, messages):
            nonlocal waiting, peak, gate
            result = await original(provider, request, messages)
            output = json.loads(result.content)
            if request.kind == "initial_outline":
                output["target_roles"] = ["product", "architect", "designer"]
                output["discussion_targets"] = [output["content"]["modules"][0]["id"], "flow", "boundary"]
            elif request.kind == "statement":
                requests[request.role] = request
                session, _ = self.service.repo.load_state(request.session_id)
                old_jobs[request.role] = next(job for job in AgentExecution()._jobs(session)
                                              if job.request.logical_task_id == request.logical_task_id)
                if gate is None:
                    gate = asyncio.Event()
                waiting += 1
                peak = max(waiting, peak)
                if waiting >= 2:
                    gate.set()
                await asyncio.wait_for(gate.wait(), timeout=2)
                waiting -= 1
                section = "modules" if request.role == "product" else "boundaries"
                target = next((entry.target_id for entry in request.working_review.entries if entry.section == section), None)
                if target and request.role != "designer":
                    output["draft_suggestions"] = [{
                        "target_id": target, "operation": "update", "proposed_change": f"Updated {section} detail",
                        "rationale": "Address a concrete gap", "source_seqs": [1],
                    }]
                else:
                    output["disposition"] = "no_new_input"
            elif request.kind == "challenge":
                output = {"text": "未发现新的阻断问题。", "no_new_issues": True, "source_seqs": [1]}
            elif request.kind == "integration":
                output["draft_decisions"] = [
                    {"suggestion_id": item.suggestion_id, "decision": "accept", "reason": "补全现有设计。"}
                    for item in request.working_review.suggestions
                ]
            elif request.kind in {"draft", "review"}:
                observed_drafts.append((request.kind, request.initial_outline, request.working_review, request.draft))
            return Completion(json.dumps(output), result.usage)

        with patch.object(SimulationProvider, "complete", simulate):
            sid, path = self.create()
        state = self.client.get(path).json()
        self.assertEqual(state["status"], "awaiting_confirmation", state)
        current = state["initial_draft"]
        self.assertEqual(current["revision"], 2)
        self.assertGreaterEqual(peak, 2)
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(request.initial_outline is None and request.draft is None for request in requests.values()))
        self.assertTrue(all(entry.section != "boundaries" for entry in requests["product"].working_review.entries))
        audit = current["working_changes"]
        old = build_service(self.db).working_draft(sid, audit["base_draft_id"])
        self.assertEqual(old["revision"], 1)
        self.assertEqual({request.working_review.draft_id for request in requests.values()}, {old["draft_id"]})
        self.assertEqual(requests["product"].working_review.topic_refs,
                         (old["working_draft"]["content"]["modules"][0]["id"], "flow", "boundary"))
        old_content = old["working_draft"]["content"]
        new_content = current["working_draft"]["content"]
        self.assertEqual([item["decision"] for item in audit["edits"]], ["accept", "accept"])
        self.assertEqual(new_content["modules"][0]["id"], old_content["modules"][0]["id"])
        self.assertEqual(new_content["modules"][0]["text"], "Updated modules detail")
        self.assertEqual(new_content["boundaries"][0]["text"], "Updated boundaries detail")
        self.assertEqual(new_content["constraints"], old_content["constraints"])
        self.assertEqual(new_content["business_flows"], old_content["business_flows"])
        self.assertEqual([entry[0] for entry in observed_drafts], ["draft", "review"])
        self.assertIsNone(observed_drafts[1][1])
        self.assertIsNone(observed_drafts[1][2])
        self.assertIsNotNone(observed_drafts[1][3])
        self.assertEqual(state["active_draft"]["based_on_working_draft_id"], current["draft_id"])
        self.assertEqual(self.client.get(f"{path}/drafts/{current['draft_id']}").status_code, 404)
        old_job = old_jobs["product"]
        self.assertFalse(AgentExecution._apply(self.service, old_job, {
            "text": "Old result", "source_seqs": [1], "disposition": "suggestion",
        }, "expired-run"))
        self.assertEqual(self.client.get(path).json()["initial_draft"]["draft_id"], current["draft_id"])

    def test_competing_updates_score_and_do_not_overwrite_the_working_draft(self):
        original = SimulationProvider.complete

        async def simulate(provider, request, messages):
            result = await original(provider, request, messages)
            output = json.loads(result.content)
            if request.kind == "initial_outline":
                output["target_roles"] = ["product", "architect"]
                output["discussion_targets"] = [output["content"]["modules"][0]["id"]]
            elif request.kind == "statement":
                output["draft_suggestions"] = [{
                    "target_id": request.working_review.topic_refs[0], "operation": "update",
                    "proposed_change": f"{request.role} prefers a different workflow",
                    "rationale": "Alternative processing order", "source_seqs": [1],
                }]
            elif request.kind == "challenge":
                output = {"text": "已检查草稿。", "source_seqs": [1], "no_new_issues": True}
            elif request.kind == "integration":
                output["draft_decisions"] = [
                    {"suggestion_id": item.suggestion_id, "decision": "accept", "reason": "尝试合并。"}
                    for item in request.working_review.suggestions
                ]
            return Completion(json.dumps(output), result.usage)

        with patch.object(SimulationProvider, "complete", simulate):
            sid, path = self.create()
        state = self.client.get(path).json()
        self.assertEqual(state["status"], "awaiting_confirmation", state)
        self.assertIsNotNone(state["scorecard"])
        current = state["initial_draft"]
        audit = current["working_changes"]
        self.assertEqual([entry["decision"] for entry in audit["edits"]], ["defer", "defer"])
        session, _ = self.service.repo.load_state(sid)
        issue = next(issue for issue in session.issues.values() if issue.kind.value == "tradeoff")
        self.assertEqual(issue.status.value, "closed")
        self.assertEqual(self.client.get(f"{path}/issues/{issue.issue_id}").json()["draft_refs"], list(issue.draft_refs))
        self.assertEqual(current["working_draft"]["content"], session.previous_outlines[-1].content["content"])

    def test_working_draft_preference_requires_user_confirmation(self):
        original = SimulationProvider.complete

        async def simulate(provider, request, messages):
            result = await original(provider, request, messages)
            output = json.loads(result.content)
            if request.kind == "initial_outline":
                output["target_roles"] = ["product"]
            elif request.kind == "statement":
                target = request.working_review.topic_refs[0]
                output["disposition"] = "needs_clarification"
                output["draft_suggestions"] = [{
                    "target_id": target, "operation": "update", "proposed_change": "向普通员工开放管理入口。",
                    "rationale": "取决于会议室管理制度。", "source_seqs": [1], "requires_user_confirmation": True,
                }]
                output["objections"] = [{
                    "description": "日常管理权限有两种方案。", "owner_role": "product", "source_seqs": [1],
                    "verification": "记录用户选择的管理权限。", "divergence": "value",
                    "user_question": "普通员工可以管理会议室吗？", "user_decision_scope": "business_policy",
                    "draft_refs": [target],
                }]
            elif request.kind == "challenge":
                output = {"text": "暂无其他问题。", "source_seqs": [1], "no_new_issues": True}
            return Completion(json.dumps(output), result.usage)

        with patch.object(SimulationProvider, "complete", simulate):
            _, path = self.create()
        state = self.client.get(path).json()
        self.assertEqual(state["waiting_for"], "user_input")
        self.assertIn("普通员工可以管理会议室吗？", state["clarification"]["questions"])
        self.assertEqual(state["initial_draft"]["revision"], 1)
        self.assertEqual(state["open_issues"][0]["kind"], "value")
        self.assertTrue(state["open_issues"][0]["draft_refs"])

    def test_invalid_delete_preserves_previous_revision_and_records_gate_failure(self):
        original = SimulationProvider.complete

        async def simulate(provider, request, messages):
            result = await original(provider, request, messages)
            output = json.loads(result.content)
            if request.kind == "initial_outline":
                output["target_roles"] = ["architect"]
            elif request.kind == "statement":
                output["draft_suggestions"] = [{
                    "target_id": request.working_review.topic_refs[0], "operation": "delete",
                    "proposed_change": "Remove the module", "rationale": "Simulate a broken flow reference",
                    "source_seqs": [1],
                }]
            elif request.kind == "challenge":
                output = {"text": "已检查草稿。", "source_seqs": [1], "no_new_issues": True}
            elif request.kind == "integration":
                output["draft_decisions"] = [
                    {"suggestion_id": item.suggestion_id, "decision": "accept", "reason": "尝试删除。"}
                    for item in request.working_review.suggestions
                ]
            return Completion(json.dumps(output), result.usage)

        with patch.object(SimulationProvider, "complete", simulate):
            sid, path = self.create()
        session, _ = self.service.repo.load_state(sid)
        state = self.client.get(path).json()
        self.assertEqual(state["status"], "awaiting_confirmation", state)
        self.assertEqual(state["initial_draft"]["revision"], 1)
        self.assertFalse(session.previous_outlines)
        integration = next(message for message in session.messages if message.rejected_working_revision)
        accepted = integration.rejected_working_revision
        self.assertEqual(accepted["quality_gate_result"]["status"], "INVALID")
        self.assertIn("reference.unknown_id", {item["rule_id"] for item in accepted["quality_gate_result"]["findings"]})

    def test_clarification_budget_and_unresolved_questions_are_enforced(self):
        original = SimulationProvider.complete

        async def ask(provider, request, messages):
            if request.kind == "initial_outline":
                return Completion(json.dumps({
                    "action": "ASK_USER", "text": "需要核对关键边界。", "source_seqs": [1],
                    "questions": [{"question_id": f"q-{i}", "question": f"请确认第 {i} 项边界？",
                                   "category": "blocking", "reason": f"缺少第 {i} 项业务事实"}
                                  for i in range(1, 5)],
                }), None)
            return await original(provider, request, messages)

        with patch.object(SimulationProvider, "complete", ask):
            _, path = self.create()
            first = self.client.get(path).json()
            self.assertEqual(len(first["clarification"]["questions"]), 3)
            self.assertTrue(first["clarification"]["details"])
            last = self.supplement(path, first, "相关业务事实目前仍然未知。")
        self.assertEqual(last["waiting_for"], "user_input")
        self.assertEqual(last["clarification"]["questions"], [])
        self.assertIsNone(last["initial_draft"])
        metrics = self.client.get(f"{path}/draft-metrics").json()
        self.assertEqual([item["asked_count"] for item in metrics["initial_clarifications"]], [3, 0])
        self.assertIn("额度", metrics["initial_clarifications"][-1]["pause_reason"])

    def test_invalid_json_is_bounded_and_explicit_retry_recovers(self):
        original = SimulationProvider.complete
        count = 0

        async def malformed(provider, request, messages):
            nonlocal count
            if request.kind == "initial_outline":
                count += 1
                if count <= 2:
                    return Completion("{", {"prompt_tokens": 11, "completion_tokens": 1, "total_tokens": 12})
            return await original(provider, request, messages)

        with patch.object(SimulationProvider, "complete", malformed):
            _, path = self.create()
            state = self.client.get(path).json()
            self.assertEqual(count, 2)
            self.assertIsNone(state["initial_draft"])
            self.assertEqual(state["waiting_for"], "host_agenda")
            self.assertIn("invalid_output", [role.get("error_code") for role in state["participants"]])
            retry = self.client.post(f"{path}/actions", json={"type": "run_agents", "expected_state_version": state["state_version"]},
                                     headers=headers())
            self.assertTrue(retry.json()["decision"]["ok"], retry.text)
            self.assertIsNotNone(self.client.get(path).json()["initial_draft"])
        metrics = self.client.get(f"{path}/draft-metrics").json()
        attempts = [entry for entry in metrics["attempts"] if entry["task_kind"] == "initial_outline"]
        self.assertEqual([entry["status"] for entry in attempts], ["FAILED", "FAILED", "SUCCEEDED"])
        self.assertEqual([entry["prompt_tokens"] for entry in attempts[:2]], [11, 11])
        self.assertTrue(attempts[1]["diagnostic"].startswith("invalid_json"))

    def test_invalid_graph_is_repaired_before_publishing(self):
        original = SimulationProvider.complete
        count = 0

        async def break_first_graph(provider, request, messages):
            nonlocal count
            result = await original(provider, request, messages)
            if request.kind == "initial_outline":
                count += 1
                if count == 1:
                    output = json.loads(result.content)
                    output["content"]["modules"][1]["depends_on"] = ["missing"]
                    return Completion(json.dumps(output), result.usage)
                self.assertIn("reference.unknown_id", messages[1]["content"])
            return result

        with patch.object(SimulationProvider, "complete", break_first_graph):
            _, path = self.create()
        self.assertEqual(count, 2)
        self.assertIsNotNone(self.client.get(path).json()["initial_draft"])
        metrics = self.client.get(f"{path}/draft-metrics").json()
        attempts = [attempt for attempt in metrics["attempts"] if attempt["task_kind"] == "initial_outline"]
        self.assertEqual([attempt["status"] for attempt in attempts], ["FAILED", "SUCCEEDED"])

    def test_duplicate_and_stale_results_cannot_replace_current_outline(self):
        complete = SimulationProvider.complete
        captured = []

        async def observe(provider, request, messages):
            response = await complete(provider, request, messages)
            if request.kind == "initial_outline" and request.input_version == 1:
                session, _ = self.service.repo.load_state(request.session_id)
                captured.append((AgentExecution()._jobs(session)[0], json.loads(response.content)))
            return response

        with patch.object(SimulationProvider, "complete", observe):
            sid, path = self.create()
        job, output = captured[0]
        first = self.client.get(path).json()
        self.assertFalse(AgentExecution._apply(self.service, job, output, "obsolete"))
        second = self.supplement(path, first, "约束：支持按天查询会议室。")
        self.assertEqual(second["initial_draft"]["input_version"], 2)
        self.assertFalse(AgentExecution._apply(self.service, job, output, "obsolete"))
        state = self.client.get(path).json()
        self.assertEqual(state["initial_draft"], second["initial_draft"])
        old = first["initial_draft"]
        self.assertEqual(self.client.get(f"{path}/working-drafts/{old['draft_id']}").json(), old)
