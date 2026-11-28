import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.discussions.routes import build_service, install
from backend.roles.provider import Completion
from backend.roles.simulation import SimulationProvider
from discussion_orchestration.engine import DiscussionEngine


IDEA = "名称：会议室预约。用户：行政人员。目标：预约不得重叠。约束：首版仅覆盖总部。"


def headers():
    return {"Idempotency-Key": str(uuid4())}


class InitialOutlineTest(unittest.TestCase):
    def test_one_host_call_publishes_outline_before_parallel_statements(self):
        original = SimulationProvider.complete
        calls = []
        captured = []

        async def observe(provider, request, messages):
            calls.append(request.kind)
            if request.kind == "statement":
                session, _ = service.repo.load_state(request.session_id)
                captured.append((session.initial_draft, session.current_draft, request.initial_outline))
            if request.kind == "review":
                self.assertIsNotNone(request.draft)
                self.assertIsNone(request.initial_outline)
            return await original(provider, request, messages)

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", observe):
            service = build_service(Path(directory) / "outline.sqlite3")
            app = FastAPI()
            install(app, service, with_agents=True, with_export=True)
            with TestClient(app) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={"idea": IDEA}, headers=headers())
                self.assertEqual(created.status_code, 201, created.text)
                path = f"/api/v1/discussions/{created.json()['id']}"
                state = client.get(path).json()
                self.assertEqual(state["status"], "awaiting_confirmation")
                outline = state["initial_draft"]
                self.assertEqual(outline["content"]["target_users"], ["行政人员"])
                self.assertEqual(outline["source_seqs"], [1, 2])
                self.assertEqual(outline["schema_version"], 1)
                self.assertEqual(outline["policy_ref"], "outline-first@1")
                self.assertTrue(outline["content"]["module_relations"])
                self.assertEqual(calls[:4], ["agenda", "statement", "statement", "statement"])
                self.assertEqual(len(captured), 3)
                for stored, frozen, in_context in captured:
                    self.assertEqual(stored.draft_id, outline["draft_id"])
                    self.assertIsNone(frozen)
                    self.assertEqual(in_context.model_dump(), outline["content"])
                self.assertEqual(state["active_draft"]["version"], 1)
                self.assertIsNone(state["active_draft"]["parent_id"])
                self.assertNotIn(outline["draft_id"], [d["draft_id"] for d in state["drafts"]])
                self.assertEqual(client.get(f"{path}/drafts/{outline['draft_id']}").status_code, 404)
                self.assertEqual(client.get(f"{path}/export", params={"draft_id": outline["draft_id"]}).status_code, 404)
                confirm = client.post(f"{path}/confirm", headers=headers(), json={
                    "draft_id": outline["draft_id"], "input_version": state["input_version"],
                    "expected_state_version": state["state_version"],
                })
                self.assertFalse(confirm.json()["decision"]["ok"], confirm.text)
                self.assertEqual(confirm.json()["decision"]["code"], "STALE_DRAFT")
                metrics = client.get(f"{path}/draft-metrics").json()
                self.assertNotIn("outline_visible", [event["stage"] for event in metrics["milestones"]])
                self.assertEqual(client.post(f"{path}/working-drafts/missing/visible").status_code, 404)
                for _ in range(2):
                    self.assertEqual(client.post(f"{path}/working-drafts/{outline['draft_id']}/visible").status_code, 200)
                metrics = client.get(f"{path}/draft-metrics").json()
                milestones = {event["stage"]: event for event in metrics["milestones"]}
                self.assertEqual(sum(event["stage"] == "outline_visible" for event in metrics["milestones"]), 1)
                self.assertEqual(set(milestones), {"input_received", "outline_committed", "outline_visible",
                                                    "candidate_frozen", "review_delivered"})
                before_outline = [attempt for attempt in metrics["attempts"]
                                  if attempt["id"] <= milestones["outline_committed"]["attempt_high_water"]]
                self.assertEqual([attempt["task_kind"] for attempt in before_outline], ["agenda"])
                self.assertIsNone(before_outline[0]["prompt_tokens"])
                self.assertIsNone(before_outline[0]["completion_tokens"])
                self.assertEqual(client.get(path).json()["state_version"], state["state_version"])
                self.assertTrue(client.delete(path).json()["deleted"])
                self.assertEqual(client.get(f"{path}/draft-metrics").status_code, 404)

    def test_root_clarification_precedes_outline_and_uses_updated_input(self):
        original = SimulationProvider.complete
        calls = []

        async def clarify(provider, request, messages):
            calls.append((request.kind, request.input_version))
            completion = await original(provider, request, messages)
            if request.kind == "agenda" and request.input_version == 1:
                output = json.loads(completion.content)
                output["initial_outline"] = {"product_name": "未完成的多余内容"}
                output.update(action="ASK_USER", target_roles=[], questions=["首版要解决哪个业务问题？"])
                return Completion(json.dumps(output, ensure_ascii=False), completion.usage)
            return completion

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", clarify):
            service = build_service(Path(directory) / "clarification.sqlite3")
            app = FastAPI()
            install(app, service, with_agents=True)
            with TestClient(app) as client:
                creation = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={"idea": "想做个工具"}, headers=headers())
                creation.raise_for_status()
                path = f"/api/v1/discussions/{creation.json()['id']}"
                state = client.get(path).json()
                self.assertEqual(calls, [("agenda", 1)])
                self.assertEqual((state["status"], state["waiting_for"], state["round_no"]), ("clarifying", "user_input", 0))
                self.assertIsNone(state["initial_draft"])
                response = client.post(f"{path}/messages", headers=headers(), json={
                    "text": IDEA, "substantive": True, "reply_to_seq": state["clarification"]["message_seq"],
                    "input_version": 1, "expected_state_version": state["state_version"],
                })
                response.raise_for_status()
                state = client.get(path).json()
                self.assertEqual(calls[:3], [("agenda", 1), ("agenda", 2), ("statement", 2)])
                self.assertEqual(state["initial_draft"]["input_version"], 2)

    def test_legacy_outline_is_readable_after_input_changes(self):
        original = DiscussionEngine.submit_agenda

        def legacy(engine, session, agenda):
            decision = original(engine, session, agenda)
            if session.input_version == 1 and session.initial_draft is not None:
                old = session.initial_draft
                session.initial_draft = replace(old, working_schema_version=None, content={
                    key: old.content[key] for key in ("product_name", "positioning", "target_users", "core_modules",
                                                      "business_flow", "system_boundary", "assumptions", "open_questions")
                })
            return decision

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(DiscussionEngine, "submit_agenda", legacy):
            service = build_service(Path(directory) / "legacy.sqlite3")
            app = FastAPI()
            install(app, service, with_agents=True)
            with TestClient(app) as client:
                creation = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={"idea": IDEA}, headers=headers())
                creation.raise_for_status()
                path = f"/api/v1/discussions/{creation.json()['id']}"
                original_state = client.get(path).json()
                old = original_state["initial_draft"]
                self.assertEqual(old["schema_version"], 0)
                self.assertEqual(old["content"]["risks"], [])
                response = client.post(f"{path}/messages", headers=headers(), json={
                    "text": "首版只允许行政人员修改预约。",
                    "substantive": True,
                    "input_version": 1,
                    "expected_state_version": original_state["state_version"],
                })
                response.raise_for_status()
                self.assertEqual(client.get(path).json()["initial_draft"]["input_version"], 2)
                self.assertEqual(client.get(f"{path}/working-drafts/{old['draft_id']}").json(), old)
                self.assertEqual(client.post(f"{path}/working-drafts/{old['draft_id']}/visible").status_code, 404)
                milestones = client.get(f"{path}/draft-metrics").json()["milestones"]
                self.assertEqual([item["input_version"] for item in milestones if item["stage"] == "input_received"],
                                 [1, 2])
                milestones = client.get(f"{path}/draft-metrics").json()["milestones"]
                self.assertEqual([item["input_version"] for item in milestones if item["stage"] == "input_received"],
                                 [1, 2])

    def test_controlled_comparison_uses_same_frozen_candidate_path(self):
        original = DiscussionEngine.submit_agenda

        for publish_outline in (False, True):
            def submit(engine, session, agenda):
                if not publish_outline:
                    agenda = replace(agenda, initial_outline=None)
                return original(engine, session, agenda)

            with self.subTest(publish_outline=publish_outline), TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                    patch.object(DiscussionEngine, "submit_agenda", submit):
                app = FastAPI()
                install(app, build_service(Path(directory) / "comparison.sqlite3"), with_agents=True)
                with TestClient(app) as client:
                    created = client.post(f"/api/v1/projects/{uuid4()}/discussions",
                                          json={"idea": IDEA}, headers=headers())
                    created.raise_for_status()
                    path = f"/api/v1/discussions/{created.json()['id']}"
                    metrics = client.get(f"{path}/draft-metrics").json()
                    milestones = {item["stage"]: item for item in metrics["milestones"]}
                    self.assertEqual("outline_committed" in milestones, publish_outline)
                    first = milestones["outline_committed" if publish_outline else "candidate_frozen"]
                    frozen = milestones["candidate_frozen"]
                    def calls_at(event):
                        return sum(attempt["id"] <= event["attempt_high_water"] for attempt in metrics["attempts"])
                    self.assertEqual(calls_at(first), 1 if publish_outline else 8)
                    self.assertEqual(calls_at(frozen), 8)
                    self.assertEqual(first["round_no"], 0 if publish_outline else 1)
                    started_at = datetime.fromisoformat(milestones["input_received"]["created_at"])
                    elapsed = (datetime.fromisoformat(first["created_at"]) - started_at).total_seconds()
                    print(f"outline={publish_outline}, first={elapsed * 1000:.0f} ms, "
                          f"calls={calls_at(first)}, round={first['round_no']}, frozen_calls={calls_at(frozen)}")

    def test_outline_milestone_includes_validation_retry(self):
        original = SimulationProvider.complete
        agenda_calls = 0

        async def fail_first_agenda(provider, request, messages):
            nonlocal agenda_calls
            completion = await original(provider, request, messages)
            if request.kind == "agenda":
                agenda_calls += 1
                if agenda_calls == 1:
                    return Completion("{}", {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
            return completion

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", fail_first_agenda):
            app = FastAPI()
            install(app, build_service(Path(directory) / "retry.sqlite3"), with_agents=True)
            with TestClient(app) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={"idea": IDEA}, headers=headers())
                created.raise_for_status()
                metrics = client.get(f"/api/v1/discussions/{created.json()['id']}/draft-metrics").json()
                outlines = [event for event in metrics["milestones"] if event["stage"] == "outline_committed"]
                self.assertEqual(len(outlines), 1)
                attempts = [attempt for attempt in metrics["attempts"]
                            if attempt["id"] <= outlines[0]["attempt_high_water"]]
                self.assertEqual([(attempt["task_kind"], attempt["status"]) for attempt in attempts],
                                 [("agenda", "FAILED"), ("agenda", "SUCCEEDED")])
                self.assertEqual(attempts[0]["prompt_tokens"], 10)
                self.assertEqual(attempts[0]["completion_tokens"], 2)
                first, retry = attempts
                self.assertLessEqual(first["started_at"], first["finished_at"])
                self.assertLessEqual(first["finished_at"], retry["started_at"])
                self.assertLessEqual(retry["started_at"], retry["finished_at"])
                self.assertLessEqual(retry["finished_at"], outlines[0]["created_at"])

    def test_missing_outline_retries_then_continues_without_fabricating_an_artifact(self):
        original = SimulationProvider.complete
        agendas = []

        async def omit_outline(provider, request, messages):
            completion = await original(provider, request, messages)
            if request.kind != "agenda":
                return completion
            agendas.append(messages)
            output = json.loads(completion.content)
            output.pop("initial_outline")
            output["target_roles"] = ["architect"]
            return Completion(json.dumps(output), completion.usage)

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}), \
                patch.object(SimulationProvider, "complete", omit_outline):
            app = FastAPI()
            install(app, build_service(Path(directory) / "missing.sqlite3"), with_agents=True)
            with TestClient(app) as client:
                created = client.post(f"/api/v1/projects/{uuid4()}/discussions", json={"idea": IDEA}, headers=headers())
                created.raise_for_status()
                path = f"/api/v1/discussions/{created.json()['id']}"
                state = client.get(path).json()
                self.assertEqual(len(agendas), 2)
                self.assertIn("add a complete initial_outline", agendas[1][1]["content"])
                self.assertIsNone(state["initial_draft"])
                self.assertIsNotNone(state["active_draft"])
                messages = client.get(f"{path}/messages").json()["items"]
                self.assertEqual([m["role"] for m in messages if m["kind"] == "statement"], ["architect"])
                self.assertIn("本次未生成初始设计框架", next(m["text"] for m in messages if m["kind"] == "agenda"))
