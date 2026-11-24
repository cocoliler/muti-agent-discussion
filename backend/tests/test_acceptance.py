"""验收测试：逐条对齐 README 成员 3 验收标准与仓库 API 契约。

与其它测试的分工：

* ``test_api.py`` 用替身服务验证路由层；
* ``test_repository.py`` 验证事务与编解码；
* 本文件用**真实引擎**跑 HTTP 与数据库，作为成员 1 内核的第一份运行证据。

README 验收标准（成员 3）：
1. 刷新或重启后可续读消息及查看历史；
2. 同一写入重试可幂等处理；
3. 跨会话引用会被拒绝。
API 契约（architecture/API_SPECIFICATION.md）：前缀 /api/v1、错误信封、幂等键、
1 MiB 上限、分页 limit 默认 20 / 最大 100、契约之外不新增路由。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from discussions import codec  # noqa: E402
from discussions.application import DiscussionService  # noqa: E402
from discussions.repository import DiscussionRepository  # noqa: E402
from discussions.routes import create_app  # noqa: E402

ENGINE_AVAILABLE = importlib.util.find_spec("discussion_orchestration") is not None
SESSION_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())

CONTRACT_ROUTES = {
    ("POST", "/api/v1/projects/{project_id}/discussions"),
    ("GET", "/api/v1/discussions/{session_id}"),
    ("GET", "/api/v1/discussions/{session_id}/messages"),
    ("POST", "/api/v1/discussions/{session_id}/messages"),
    ("POST", "/api/v1/discussions/{session_id}/actions"),
    ("POST", "/api/v1/discussions/{session_id}/confirm"),
}


def contract_routes(app) -> set[tuple[str, str]]:
    """按 OpenAPI 产物取路由集合——与仓库漂移检查的比对口径一致。"""
    found: set[tuple[str, str]] = set()
    for path, operations in app.openapi().get("paths", {}).items():
        if not path.startswith("/api/v1"):
            continue
        for method in operations:
            if method.lower() in {"head", "options", "parameters"}:
                continue
            found.add((method.upper(), path))
    return found


@unittest.skipUnless(ENGINE_AVAILABLE, "需要仓库内的 discussion_orchestration 包")
class AcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "acceptance.sqlite3"
        self.app, self.service = self._build()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build(self):
        repo = DiscussionRepository(self.db_path)
        repo.init_schema()
        repo.mark_interrupted_attempts()
        service = DiscussionService(repo)
        return create_app(service), service

    # ------------------------------------------------------------------ 工具

    def create_session(self, idea: str = "做一个内部会议室预约工具", **extra) -> dict:
        payload = {"idea": idea, **extra}
        response = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/discussions",
            json=payload,
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def view(self, sid: str) -> dict:
        response = self.client.get(f"/api/v1/discussions/{sid}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def post_text(self, sid: str, text: str, *, key: str | None = None, **extra):
        return self.client.post(
            f"/api/v1/discussions/{sid}/messages",
            json={"text": text, "expected_state_version": self.view(sid)["state_version"], **extra},
            headers={"Idempotency-Key": key or str(uuid.uuid4())},
        )

    # ------------------------------------------------------------------ 契约

    def test_route_set_matches_contract(self) -> None:
        """§6 只定义 6 个讨论操作；私加路由会被 export_openapi 的漂移检查拦下。"""
        self.assertEqual(contract_routes(self.app), CONTRACT_ROUTES)

    def test_error_envelope_has_required_fields(self) -> None:
        response = self.client.get("/api/v1/discussions/not-a-uuid")
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertTrue({"code", "message", "issues", "request_id"} <= set(body["error"]))
        self.assertEqual(body["error"]["code"], "VALIDATION_FAILED")
        self.assertTrue(body["error"]["request_id"])

    # ------------------------------------------------------------------ 创建与额度

    def test_create_returns_clarifying_session(self) -> None:
        body = self.create_session()
        self.assertEqual(body["status"], "clarifying")
        self.assertEqual(body["waiting_for"], "host_agenda")
        self.assertEqual(body["last_seq"], 1)
        self.assertEqual(body["input_version"], 1)
        self.assertEqual(self.view(body["id"])["id"], body["id"])

    def test_custom_limits_take_effect(self) -> None:
        """引擎 create_session 不接收 limits，额度必须由服务层挂载后才生效。"""
        body = self.create_session(limits={"max_total_rounds": 3})
        self.assertEqual(body["budget"]["rounds_left"], 3)

    def test_create_is_idempotent(self) -> None:
        key = str(uuid.uuid4())
        first = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/discussions",
            json={"idea": "同一想法"},
            headers={"Idempotency-Key": key},
        )
        second = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/discussions",
            json={"idea": "同一想法"},
            headers={"Idempotency-Key": key},
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["id"], second.json()["id"])

    # ------------------------------------------------------------------ 续读与分页

    def test_incremental_pagination_has_no_gap_or_duplicate(self) -> None:
        sid = self.create_session()["id"]
        for index in range(6):
            response = self.post_text(sid, f"补充 {index}", substantive=False)
            self.assertEqual(response.status_code, 200, response.text)

        seen: list[int] = []
        cursor = 0
        while True:
            page = self.client.get(
                f"/api/v1/discussions/{sid}/messages",
                params={"after_seq": cursor, "limit": 2},
            ).json()
            seen.extend(item["seq"] for item in page["items"])
            if page["next_cursor"] is None:
                break
            cursor = int(page["next_cursor"])
        self.assertEqual(seen, list(range(1, len(seen) + 1)))
        self.assertEqual(len(seen), 7)

    def test_restart_recovers_messages_and_state(self) -> None:
        """README 验收：刷新或重启后可续读消息及查看历史。"""
        sid = self.create_session()["id"]
        self.post_text(sid, "补充：只覆盖一个办公地点", substantive=False)
        before = self.view(sid)
        before_msgs = self.client.get(f"/api/v1/discussions/{sid}/messages").json()["items"]

        # 换连接、换服务实例，模拟进程重启
        self.client = TestClient(self._build()[0], raise_server_exceptions=False)

        after = self.view(sid)
        after_msgs = self.client.get(f"/api/v1/discussions/{sid}/messages").json()["items"]
        self.assertEqual(before, after)
        self.assertEqual(before_msgs, after_msgs)
        # 重启后仍可继续推进
        self.assertEqual(self.post_text(sid, "重启后补充", substantive=False).status_code, 200)

    def test_get_does_not_advance_session(self) -> None:
        """刷新不触发模型重跑：读请求不改变状态版本。"""
        sid = self.create_session()["id"]
        first = self.view(sid)
        for _ in range(3):
            self.client.get(f"/api/v1/discussions/{sid}/messages")
            self.view(sid)
        self.assertEqual(self.view(sid)["state_version"], first["state_version"])

    # ------------------------------------------------------------------ 幂等

    def test_same_write_retried_is_idempotent(self) -> None:
        sid = self.create_session()["id"]
        key = str(uuid.uuid4())
        payload = {
            "text": "重试同一条",
            "expected_state_version": self.view(sid)["state_version"],
            "substantive": False,
        }
        first = self.client.post(
            f"/api/v1/discussions/{sid}/messages", json=payload, headers={"Idempotency-Key": key}
        )
        second = self.client.post(
            f"/api/v1/discussions/{sid}/messages", json=payload, headers={"Idempotency-Key": key}
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        messages = self.client.get(f"/api/v1/discussions/{sid}/messages").json()["items"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(self.view(sid)["state_version"], 2)

    def test_same_key_different_body_conflicts(self) -> None:
        sid = self.create_session()["id"]
        key = str(uuid.uuid4())
        version = self.view(sid)["state_version"]
        self.client.post(
            f"/api/v1/discussions/{sid}/messages",
            json={"text": "A", "expected_state_version": version, "substantive": False},
            headers={"Idempotency-Key": key},
        )
        response = self.client.post(
            f"/api/v1/discussions/{sid}/messages",
            json={"text": "B", "expected_state_version": version, "substantive": False},
            headers={"Idempotency-Key": key},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_validation_failure_does_not_consume_key(self) -> None:
        sid = self.create_session()["id"]
        key = str(uuid.uuid4())
        version = self.view(sid)["state_version"]
        bad = self.client.post(
            f"/api/v1/discussions/{sid}/messages",
            json={"text": "引用越界", "expected_state_version": version, "reply_to_seq": 999},
            headers={"Idempotency-Key": key},
        )
        self.assertEqual(bad.status_code, 422)
        good = self.client.post(
            f"/api/v1/discussions/{sid}/messages",
            json={"text": "合法补充", "expected_state_version": version, "substantive": False},
            headers={"Idempotency-Key": key},
        )
        self.assertEqual(good.status_code, 200, good.text)

    # ------------------------------------------------------------------ 冲突与引用

    def test_stale_state_version_conflicts(self) -> None:
        sid = self.create_session()["id"]
        stale = self.view(sid)["state_version"]
        self.assertEqual(self.post_text(sid, "第一条", substantive=False).status_code, 200)
        response = self.client.post(
            f"/api/v1/discussions/{sid}/messages",
            json={"text": "拿旧版本写", "expected_state_version": stale},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "REVISION_CONFLICT")

    def test_cross_session_reference_rejected(self) -> None:
        """README 验收：跨会话引用会被拒绝。"""
        first = self.create_session("想法 A")["id"]
        second = self.create_session("想法 B")["id"]
        for index in range(3):
            self.post_text(first, f"A-{index}", substantive=False)
        # 序号 4 只存在于会话 A，会话 B 只有 1 条
        response = self.client.post(
            f"/api/v1/discussions/{second}/messages",
            json={
                "text": "引用别的会话",
                "expected_state_version": self.view(second)["state_version"],
                "reply_to_seq": 4,
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_FAILED")

    def test_stale_input_version_rejected(self) -> None:
        sid = self.create_session()["id"]
        response = self.client.post(
            f"/api/v1/discussions/{sid}/messages",
            json={
                "text": "基于旧上下文",
                "expected_state_version": self.view(sid)["state_version"],
                "input_version": 9,
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 409)

    # ------------------------------------------------------------------ 编排拒绝

    def test_confirm_in_wrong_state_is_business_rejection(self) -> None:
        """非法阶段由签收门禁拦住：200 + decision.ok=false，且状态不推进。

        门禁的实现方是 spec_handoff（成员 5），错误码取自它的 Finding，
        因此这里断言的是一份可定位的 findings，而不是 HTTP 4xx。
        """
        sid = self.create_session()["id"]
        before = self.view(sid)["state_version"]
        response = self.client.post(
            f"/api/v1/discussions/{sid}/confirm",
            json={
                "draft_id": "d-1",
                "input_version": 1,
                "expected_state_version": before,
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["decision"]["ok"])
        self.assertEqual(body["decision"]["code"], "INVALID_STATUS")
        findings = body["result"]["findings"]
        self.assertEqual(findings[0]["path"], "/status")
        # 拒绝不得推进状态，也不得写签收记录
        self.assertEqual(self.view(sid)["state_version"], before)
        self.assertIsNone(self.service.repo.get_handoff(sid))

    # ------------------------------------------------------------------ 投影

    def test_derived_tables_follow_session_state(self) -> None:
        """草稿与评审必须投影到表：历史查看、差异视图和导出都读表。"""
        from discussion_orchestration import DraftRevision, ReviewFinding, ReviewResult
        from discussion_orchestration.enums import Role, Severity, Verdict

        sid = self.create_session()["id"]
        session, meta = self.service.repo.load_state(sid)
        session.drafts.append(
            DraftRevision(
                draft_id="d-1",
                version=1,
                input_version=1,
                content={"product_name": "会议室预约"},
                source_seqs=(1,),
            )
        )
        session.reviews.append(
            ReviewResult(
                review_id="r-1",
                session_id=sid,
                draft_id="d-1",
                input_version=1,
                verdict=Verdict.REVISE,
                findings=(
                    ReviewFinding(
                        finding_id="f-1",
                        severity=Severity.BLOCKING,
                        target_item="FR-01",
                        summary="缺少并发约束",
                        close_condition="补充验收条件",
                        responsible_role=Role.ARCHITECT,
                    ),
                ),
            )
        )
        self.service.repo.save_after_decision(session, meta["state_version"], action="seed")

        history = self.service.repo.history(sid)
        self.assertEqual([d["id"] for d in history["drafts"]], ["d-1"])
        self.assertEqual(history["drafts"][0]["content"]["product_name"], "会议室预约")
        self.assertEqual([r["verdict"] for r in history["reviews"]], ["revise"])
        self.assertEqual(history["reviews"][0]["findings"][0]["finding_id"], "f-1")

        # 重启后历史仍可查看（读表而非内存）
        _, meta2 = self._build()[1].repo.load_state(sid)
        self.assertEqual(meta2["state_version"], meta["state_version"] + 1)


@unittest.skipUnless(ENGINE_AVAILABLE, "需要仓库内的 discussion_orchestration 包")
class MirrorIntegrityTest(unittest.TestCase):
    def test_projection_sequence_is_contiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "mirror.sqlite3"
            repo = DiscussionRepository(db_path)
            repo.init_schema()
            service = DiscussionService(repo)
            session = service.engine.create_session(
                session_id=str(uuid.uuid4()), project_id=str(uuid.uuid4()), idea="想法"
            )
            repo.create_session(session)
            for index in range(5):
                fresh, meta = repo.load_state(session.session_id)
                service.engine.note_user_input(fresh, f"补充 {index}", substantive=False)
                repo.save_after_decision(fresh, meta["state_version"])
            rows = repo.list_messages_after(session.session_id, 0, 100)[0]
            self.assertEqual([row["seq"] for row in rows], list(range(1, 7)))
            raw = codec.dumps(repo.load_state(session.session_id)[0])
            self.assertIn('"session_id"', raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
