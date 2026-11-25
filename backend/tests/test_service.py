"""协调服务测试（实施方案 §9）：验证写路径的事务、幂等与版本冲突。

用替身引擎隔离成员 1 的实现，只检验本层的接线是否正确；
真实引擎的联调见 ``test_engine_contract.py``。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discussions import codec  # noqa: E402
from discussions.application import DiscussionService  # noqa: E402
from discussions.errors import ApiError  # noqa: E402
from discussions.repository import DiscussionRepository  # noqa: E402


class Status(str, Enum):
    CLARIFYING = "clarifying"
    DISCUSSING = "discussing"


class Role(str, Enum):
    USER = "user"
    ARCHITECT = "architect"


class Kind(str, Enum):
    USER_INPUT = "user_input"
    STATEMENT = "statement"


class Waiting(str, Enum):
    HOST_AGENDA = "host_agenda"
    ROLE_RESULTS = "role_results"


class Code(str, Enum):
    OK = "ok"
    AWAITING_HOST_DECISION = "awaiting_host_decision"


@dataclass(frozen=True)
class Msg:
    seq: int
    role: Role
    kind: Kind
    text: str
    source_seqs: tuple[int, ...] = ()
    reply_to_seq: int | None = None
    item_refs: tuple[str, ...] = ()
    issue_ids: tuple[str, ...] = ()
    input_version: int = 1
    round_no: int = 0
    topic_id: str | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class Limits:
    max_total_rounds: int = 12
    max_directed_rounds: int = 2
    max_revision_rounds: int = 2
    max_naming_passes_per_round: int = 2
    max_dispatch_units: int = 64


@dataclass
class Sess:
    session_id: str
    project_id: str = "p-1"
    status: Status = Status.CLARIFYING
    input_version: int = 1
    round_no: int = 0
    round_kind: object | None = None
    phase: object | None = None
    topic_id: str | None = None
    waiting_for: Waiting = Waiting.HOST_AGENDA
    messages: list[Msg] = field(default_factory=list)
    reviews: list = field(default_factory=list)
    limits: Limits = field(default_factory=Limits)
    directed_rounds: int = 0
    revision_rounds: int = 0
    dispatch_units: int = 0

    @property
    def last_seq(self) -> int:
        return self.messages[-1].seq if self.messages else 0

    @property
    def current_draft(self):
        return None

    def open_issues(self):
        return ()


class Decision:
    def __init__(self, ok: bool = True, code: Code = Code.OK, seq: int | None = None) -> None:
        self.ok = ok
        self.code = code
        self.message = ""
        self.tasks = ()
        self.seq = seq
        self.waiting_for = Waiting.HOST_AGENDA


class FakeEngine:
    """只实现本层用到的引擎入口，行为与真实引擎的契约一致。"""

    def __init__(self) -> None:
        self.calls = 0

    def create_session(self, *, session_id, project_id, idea, limits=None):
        session = Sess(session_id=session_id, project_id=project_id, limits=limits or Limits())
        session.messages.append(Msg(seq=1, role=Role.USER, kind=Kind.USER_INPUT, text=idea))
        return session

    def note_user_input(self, session, text, *, reply_to_seq=None, substantive=True):
        self.calls += 1
        seq = session.last_seq + 1
        session.messages.append(
            Msg(seq=seq, role=Role.USER, kind=Kind.USER_INPUT, text=text,
                reply_to_seq=reply_to_seq, input_version=session.input_version)
        )
        if substantive:
            session.input_version += 1
        return Decision(seq=seq)


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = DiscussionRepository(
            Path(self._tmp.name) / "svc.sqlite3", dumper=codec.dumps, loader=lambda raw: codec.loads(raw, Sess)
        )
        self.repo.init_schema()
        self.engine = FakeEngine()
        self.service = DiscussionService(self.repo, engine=self.engine, models={})
        self.view = self.service.create("p-1", "做一个会议室预约工具")[1]
        self.sid = self.view["id"]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_renders_initial_view(self) -> None:
        self.assertEqual(self.view["status"], "clarifying")
        self.assertEqual(self.view["last_seq"], 1)
        self.assertEqual(self.view["state_version"], 1)
        self.assertEqual(self.view["waiting_for"], "host_agenda")
        self.assertNotIn("budget", self.view)

    def test_post_message_commits_and_projects(self) -> None:
        status, body = self.service.post_message(
            self.sid,
            text="补充：只覆盖一个办公地点",
            reply_to_seq=1,
            substantive=True,
            expected_state_version=1,
            idempotency_key="k-1",
            request_hash="h-1",
            scope=f"POST /api/v1/discussions/{self.sid}/messages",
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["decision"]["ok"])
        self.assertEqual(len(body["new_messages"]), 1)
        self.assertEqual(body["new_messages"][0]["seq"], 2)
        self.assertEqual(body["session"]["state_version"], 2)
        self.assertEqual(body["session"]["input_version"], 2)

        items, _ = self.repo.list_messages_after(self.sid, 0, 10)
        self.assertEqual([item["seq"] for item in items], [1, 2])

    def test_replay_same_key_returns_original_body(self) -> None:
        kwargs = dict(
            text="补充",
            reply_to_seq=None,
            substantive=False,
            expected_state_version=1,
            idempotency_key="k-replay",
            request_hash="h-replay",
            scope=f"POST /api/v1/discussions/{self.sid}/messages",
        )
        _, first = self.service.post_message(self.sid, **kwargs)
        _, second = self.service.post_message(self.sid, **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(self.engine.calls, 1)
        items, _ = self.repo.list_messages_after(self.sid, 0, 10)
        self.assertEqual(len(items), 2)

    def test_same_key_different_body_conflicts(self) -> None:
        self.service.post_message(
            self.sid, text="A", reply_to_seq=None, substantive=False, expected_state_version=1,
            idempotency_key="k-x", request_hash="h-a",
            scope=f"POST /api/v1/discussions/{self.sid}/messages",
        )
        with self.assertRaises(ApiError) as ctx:
            self.service.post_message(
                self.sid, text="B", reply_to_seq=None, substantive=False, expected_state_version=1,
                idempotency_key="k-x", request_hash="h-b",
                scope=f"POST /api/v1/discussions/{self.sid}/messages",
            )
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(ctx.exception.status, 409)

    def test_stale_state_version_rejected(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.service.post_message(
                self.sid, text="补充", reply_to_seq=None, substantive=False, expected_state_version=9,
                idempotency_key="k-y", request_hash="h-y",
                scope=f"POST /api/v1/discussions/{self.sid}/messages",
            )
        self.assertEqual(ctx.exception.code, "REVISION_CONFLICT")
        self.assertEqual(ctx.exception.detail["state_version"], 1)

    def test_cross_session_reference_rejected(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.service.post_message(
                self.sid, text="引用不存在的消息", reply_to_seq=99, substantive=False,
                expected_state_version=1, idempotency_key="k-z", request_hash="h-z",
                scope=f"POST /api/v1/discussions/{self.sid}/messages",
            )
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "VALIDATION_FAILED")

    def test_rejection_does_not_consume_idempotency_key(self) -> None:
        with self.assertRaises(ApiError):
            self.service.post_message(
                self.sid, text="x", reply_to_seq=99, substantive=False, expected_state_version=1,
                idempotency_key="k-free", request_hash="h-free",
                scope=f"POST /api/v1/discussions/{self.sid}/messages",
            )
        self.assertIsNone(self.repo.get_idempotent(f"POST /api/v1/discussions/{self.sid}/messages", "k-free"))

    def test_messages_pagination_via_service(self) -> None:
        page = self.service.messages(self.sid, 0, 1)
        self.assertEqual(len(page["items"]), 1)
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_cursor"])

    def test_messages_pagination_reports_cursor(self) -> None:
        self.service.post_message(
            self.sid, text="补充", reply_to_seq=None, substantive=False, expected_state_version=1,
            idempotency_key="k-page", request_hash="h-page",
            scope=f"POST /api/v1/discussions/{self.sid}/messages",
        )
        page = self.service.messages(self.sid, 0, 1)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_cursor"], "1")

    def test_stale_input_version_rejected(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.service.post_message(
                self.sid, text="基于旧上下文", reply_to_seq=None, substantive=True,
                expected_state_version=1, input_version=7,
                idempotency_key="k-iv", request_hash="h-iv",
                scope=f"POST /api/v1/discussions/{self.sid}/messages",
            )
        self.assertEqual(ctx.exception.code, "REVISION_CONFLICT")
        self.assertEqual(ctx.exception.status, 409)

    def test_unknown_session_is_404(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.service.view("00000000-0000-4000-8000-000000000000")
        self.assertEqual(ctx.exception.status, 404)

    # ------------------------------------------------------------------ 签收正文

    def test_export_reads_signed_content(self) -> None:
        """导出读的是签收时落库的正文，而不是当前草稿——签收后草稿可能已被改动。"""
        self.repo.record_handoff(
            session_id=self.sid,
            draft_id="d-1",
            review_id="r-1",
            input_version=1,
            content_hash="sha256:deadbeef",
            content_json='{"schema_version": "1.0", "product_name": "会议室预约"}',
        )
        exported = self.service.export_content(self.sid)
        self.assertEqual(exported["content"]["schema_version"], "1.0")
        self.assertEqual(exported["content_hash"], "sha256:deadbeef")
        self.assertEqual(exported["draft_id"], "d-1")

    def test_export_without_content_is_409(self) -> None:
        """只有签收记录、没有正文时不得导出空壳。"""
        self.repo.record_handoff(
            session_id=self.sid, draft_id="d-1", review_id="r-1", input_version=1
        )
        with self.assertRaises(ApiError) as ctx:
            self.service.export_content(self.sid)
        self.assertEqual(ctx.exception.status, 409)

    def test_export_before_confirm_is_409(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.service.export_content(self.sid)
        self.assertEqual(ctx.exception.status, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
