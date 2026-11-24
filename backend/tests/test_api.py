"""接口层契约测试（实施方案 §7、T6/T7/T8 的 HTTP 侧）。

用替身服务验证路由、幂等键要求、错误信封与状态码，不依赖引擎与数据库。
"""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from discussions.errors import ApiError  # noqa: E402
from discussions.repository import StorageBusy, VersionConflict  # noqa: E402
from discussions.routes import create_app, install  # noqa: E402

SESSION_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
KEY = str(uuid.uuid4())


def session_view(**overrides):
    view = {
        "id": SESSION_ID,
        "project_id": PROJECT_ID,
        "status": "clarifying",
        "input_version": 1,
        "round_no": 0,
        "round_kind": None,
        "phase": None,
        "topic_id": None,
        "waiting_for": "host_agenda",
        "last_seq": 1,
        "state_version": 1,
        "participants": [{"role": "user", "status": "done", "attempt_no": None}],
        "active_draft": None,
        "open_issues": [],
        "budget": {"rounds_left": 12},
    }
    view.update(overrides)
    return view


class FakeService:
    export_enabled = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def create(self, project_id, idea, limits, **kwargs):
        self.calls.append(("create", {"project_id": project_id, "idea": idea, "limits": limits, **kwargs}))
        return 201, session_view()

    def view(self, session_id):
        if session_id == "00000000-0000-4000-8000-000000000000":
            raise ApiError("NOT_FOUND", "找不到讨论会话", status=404)
        return session_view()

    def messages(self, session_id, after_seq, limit):
        self.calls.append(("messages", {"after_seq": after_seq, "limit": limit}))
        return {
            "items": [
                {
                    "seq": 2,
                    "role": "architect",
                    "kind": "statement",
                    "text": "并发要放在同一事务内",
                    "source_seqs": [],
                    "item_refs": ["FR-02"],
                    "issue_ids": [],
                    "input_version": 1,
                    "round_no": 1,
                }
            ],
            "has_more": False,
            "last_seq": 2,
            "next_cursor": None,
        }

    def post_message(self, session_id, **kwargs):
        self.calls.append(("post_message", kwargs))
        if kwargs["expected_state_version"] == 99:
            raise VersionConflict(session_id, 99, 7)
        if kwargs["expected_state_version"] == 88:
            raise StorageBusy("database is locked")
        return 200, {
            "decision": {"ok": True, "code": "ok", "waiting_for": "host_agenda", "seq": 2},
            "session": session_view(state_version=2, last_seq=2),
            "new_messages": [],
            "tasks": [],
        }

    def act(self, session_id, **kwargs):
        self.calls.append(("act", kwargs))
        if kwargs["action"] == "boom":
            raise ApiError("VALIDATION_FAILED", "不支持的动作", status=422)
        return 200, {"decision": {"ok": False, "code": "awaiting_review"}, "session": None,
                     "new_messages": [], "tasks": []}

    def confirm(self, session_id, **kwargs):
        return 200, {
            "decision": {"ok": True, "code": "ok"},
            "handoff": {
                "status": "confirmed",
                "draft_id": kwargs["draft_id"],
                "review_id": "r-1",
                "input_version": kwargs["input_version"],
                "spec_expected_revision": kwargs["spec_expected_revision"],
                "confirmed_at": "2026-11-23T00:00:00Z",
                "export_url": f"/api/v1/discussions/{session_id}/export" if self.export_enabled else None,
            },
            "session": session_view(status="completed", state_version=3),
        }

    def history(self, session_id):
        return {"drafts": [], "reviews": [], "attempts": [], "handoff": None}

    def export_content(self, session_id):
        raise ApiError("VALIDATION_FAILED", "尚未签收，无法导出", status=409)


class RoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.client = TestClient(create_app(self.service), raise_server_exceptions=False)

    def test_create_discussion_returns_201(self) -> None:
        response = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/discussions",
            json={"idea": "做一个内部会议室预约工具"},
            headers={"Idempotency-Key": KEY},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "clarifying")
        self.assertEqual(self.service.calls[0][1]["project_id"], PROJECT_ID)
        self.assertEqual(self.service.calls[0][1]["idempotency_key"], KEY)
        self.assertEqual(self.service.calls[0][1]["scope"], f"POST /api/v1/projects/{PROJECT_ID}/discussions")

    def test_create_requires_idempotency_key(self) -> None:
        response = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/discussions",
            json={"idea": "做一个内部会议室预约工具"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_FAILED")

    def test_invalid_uuid_rejected(self) -> None:
        response = self.client.get("/api/v1/discussions/not-a-uuid")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_FAILED")

    def test_not_found_envelope(self) -> None:
        response = self.client.get("/api/v1/discussions/00000000-0000-4000-8000-000000000000")
        self.assertEqual(response.status_code, 404)
        body = response.json()["error"]
        self.assertEqual(body["code"], "NOT_FOUND")
        self.assertEqual(body["issues"], [])
        self.assertTrue(body["request_id"])

    def test_request_id_generated_when_absent(self) -> None:
        response = self.client.get("/api/v1/discussions/not-a-uuid")
        self.assertTrue(response.json()["error"]["request_id"])

    def test_messages_pagination_view(self) -> None:
        response = self.client.get(f"/api/v1/discussions/{SESSION_ID}/messages?after_seq=1&limit=10")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["items"][0]["seq"], 2)
        self.assertIsNone(payload["next_cursor"])
        self.assertEqual(self.service.calls[0][1]["limit"], 10)

    def test_limit_defaults_to_20_and_caps_at_100(self) -> None:
        self.client.get(f"/api/v1/discussions/{SESSION_ID}/messages")
        self.assertEqual(self.service.calls[-1][1]["limit"], 20)
        over = self.client.get(f"/api/v1/discussions/{SESSION_ID}/messages?limit=101")
        self.assertEqual(over.status_code, 422)
        zero = self.client.get(f"/api/v1/discussions/{SESSION_ID}/messages?limit=0")
        self.assertEqual(zero.status_code, 422)

    def test_unknown_field_rejected(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/messages",
            json={"text": "补充", "expected_state_version": 1, "unexpected": True},
            headers={"Idempotency-Key": KEY},
        )
        self.assertEqual(response.status_code, 422)

    def test_write_requires_idempotency_key(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/messages",
            json={"text": "补充", "expected_state_version": 1},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_FAILED")

    def test_idempotency_key_must_be_uuid(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/messages",
            json={"text": "补充", "expected_state_version": 1},
            headers={"Idempotency-Key": "abc"},
        )
        self.assertEqual(response.status_code, 422)

    def test_post_message_passes_contract_fields(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/messages",
            json={"text": "补充: 只覆盖一个办公地点", "expected_state_version": 1,
                  "reply_to_seq": 1, "substantive": True, "input_version": 1},
            headers={"Idempotency-Key": KEY, "X-Request-Id": "req-1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["decision"]["code"], "ok")
        _, kwargs = self.service.calls[-1]
        self.assertEqual(kwargs["idempotency_key"], KEY)
        self.assertEqual(kwargs["request_id"], "req-1")
        self.assertEqual(kwargs["input_version"], 1)
        self.assertEqual(kwargs["scope"], f"POST /api/v1/discussions/{SESSION_ID}/messages")
        self.assertEqual(len(kwargs["request_hash"]), 64)

    def test_stale_state_version_maps_to_409(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/messages",
            json={"text": "补充", "expected_state_version": 99},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 409)
        body = response.json()["error"]
        self.assertEqual(body["code"], "REVISION_CONFLICT")
        self.assertEqual(body["detail"]["state_version"], 7)

    def test_storage_busy_maps_to_503(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/messages",
            json={"text": "补充", "expected_state_version": 88},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "STORAGE_BUSY")

    def test_orchestration_rejection_is_200(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/actions",
            json={"type": "resume", "expected_state_version": 2},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["decision"]["ok"])
        self.assertEqual(payload["decision"]["code"], "awaiting_review")

    def test_action_validation_error_is_422(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/actions",
            json={"type": "boom", "expected_state_version": 2},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        # ``boom`` 不是合法枚举，先被 Pydantic 拦住
        self.assertEqual(response.status_code, 422)

    def test_confirm_returns_handoff(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/confirm",
            json={"draft_id": "d-2", "input_version": 1, "expected_state_version": 2,
                  "spec_expected_revision": 0},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 200)
        handoff = response.json()["handoff"]
        self.assertEqual(handoff["status"], "confirmed")
        self.assertEqual(handoff["spec_expected_revision"], 0)
        # 导出未登记契约前不返回可用链接
        self.assertIsNone(handoff["export_url"])

    def test_export_route_not_registered_by_default(self) -> None:
        """§6 只定义 6 个讨论操作；导出属候选第 7 个，默认不暴露。"""
        response = self.client.get(f"/api/v1/discussions/{SESSION_ID}/export")
        self.assertEqual(response.status_code, 404)

    def test_export_route_when_enabled(self) -> None:
        service = FakeService()
        app = FastAPI()
        install(app, service, with_export=True)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(f"/api/v1/discussions/{SESSION_ID}/export")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_FAILED")


class PayloadLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.client = TestClient(create_app(self.service), raise_server_exceptions=False)

    def test_oversized_body_is_413(self) -> None:
        body = {"text": "x" * (1024 * 1024 + 16), "expected_state_version": 1}
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/messages",
            json=body,
            headers={"Idempotency-Key": KEY},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "PAYLOAD_TOO_LARGE")

    def test_normal_body_passes(self) -> None:
        response = self.client.post(
            f"/api/v1/discussions/{SESSION_ID}/messages",
            json={"text": "补充", "expected_state_version": 1},
            headers={"Idempotency-Key": KEY},
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
