"""仓储层测试（实施方案 §6、T4/T5/T6 的存储部分）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discussions import codec  # noqa: E402
from discussions.repository import DiscussionRepository, NotFound, VersionConflict  # noqa: E402


class Status(str, Enum):
    CLARIFYING = "clarifying"
    DISCUSSING = "discussing"
    COMPLETED = "completed"


class Role(str, Enum):
    USER = "user"
    ARCHITECT = "architect"


class Kind(str, Enum):
    USER_INPUT = "user_input"
    STATEMENT = "statement"


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


@dataclass
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
    messages: list[Msg] = field(default_factory=list)
    limits: Limits = field(default_factory=Limits)
    nested: dict[Role, set[str]] = field(default_factory=dict)

    @property
    def last_seq(self) -> int:
        return self.messages[-1].seq if self.messages else 0


def new_session(session_id: str = "s-1") -> Sess:
    return Sess(
        session_id=session_id,
        messages=[Msg(seq=1, role=Role.USER, kind=Kind.USER_INPUT, text="做一个会议室预约工具")],
        nested={Role.ARCHITECT: {"I-1"}},
    )


class RepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "test.sqlite3"
        self.repo = DiscussionRepository(self.db, dumper=codec.dumps, loader=lambda raw: codec.loads(raw, Sess))
        self.repo.init_schema()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------ 基础

    def test_init_schema_is_idempotent(self) -> None:
        self.repo.init_schema()
        self.repo.init_schema()
        with self.repo.connect() as conn:
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in (
            "discussion_sessions",
            "discussion_messages",
            "role_attempts",
            "draft_revisions",
            "review_results",
            "spec_handoffs",
            "idempotency_keys",
            "discussion_events",
        ):
            self.assertIn(table, tables)

    def test_create_and_reload(self) -> None:
        session = new_session()
        meta = self.repo.create_session(session)
        self.assertEqual(meta["state_version"], 1)
        self.assertEqual(meta["last_seq"], 1)

        restored, meta2 = self.repo.load_state("s-1")
        self.assertEqual(restored, session)
        self.assertEqual(meta2["status"], "clarifying")

    def test_unknown_session_raises(self) -> None:
        with self.assertRaises(NotFound):
            self.repo.load_state("nope")

    # ------------------------------------------------------------------ seq 镜像

    def test_save_appends_only_new_messages(self) -> None:
        session = new_session()
        self.repo.create_session(session)

        session.messages.append(Msg(seq=2, role=Role.ARCHITECT, kind=Kind.STATEMENT, text="并发要加事务"))
        meta = self.repo.save_after_decision(session, 1)
        self.assertEqual(meta["state_version"], 2)
        self.assertEqual(meta["last_seq"], 2)

        items, has_more = self.repo.list_messages_after("s-1", 0, 50)
        self.assertEqual([item["seq"] for item in items], [1, 2])
        self.assertFalse(has_more)
        self.assertEqual(items[1]["role"], "architect")

        # 再存一次但不新增消息：不得重复插入
        self.repo.save_after_decision(session, 2)
        items, _ = self.repo.list_messages_after("s-1", 0, 50)
        self.assertEqual([item["seq"] for item in items], [1, 2])

    def test_projection_gap_is_rejected(self) -> None:
        from discussions.repository import DuplicateProjection

        session = new_session()
        self.repo.create_session(session)
        session.messages.append(Msg(seq=5, role=Role.ARCHITECT, kind=Kind.STATEMENT, text="跳号"))
        with self.assertRaises(DuplicateProjection):
            self.repo.save_after_decision(session, 1)

    # ------------------------------------------------------------------ 并发

    def test_stale_state_version_conflicts(self) -> None:
        session = new_session()
        self.repo.create_session(session)
        session.messages.append(Msg(seq=2, role=Role.ARCHITECT, kind=Kind.STATEMENT, text="a"))
        self.repo.save_after_decision(session, 1)

        with self.assertRaises(VersionConflict) as ctx:
            self.repo.save_after_decision(session, 1)
        self.assertEqual(ctx.exception.current, 2)

    # ------------------------------------------------------------------ 分页

    def test_after_seq_pagination(self) -> None:
        session = Sess(session_id="s-page")
        session.messages = [
            Msg(seq=i, role=Role.ARCHITECT, kind=Kind.STATEMENT, text=f"m{i}") for i in range(1, 121)
        ]
        self.repo.create_session(session)

        seen: list[int] = []
        cursor = 0
        while True:
            items, has_more = self.repo.list_messages_after("s-page", cursor, 50)
            seen.extend(item["seq"] for item in items)
            if not has_more:
                break
            cursor = items[-1]["seq"]
        self.assertEqual(seen, list(range(1, 121)))

    # ------------------------------------------------------------------ 幂等

    def test_idempotency_replay_and_conflict(self) -> None:
        self.repo.put_idempotent("POST /x", "key-1", "hash-a", 200, '{"ok":true}')
        hit = self.repo.get_idempotent("POST /x", "key-1")
        self.assertEqual(hit["request_hash"], "hash-a")
        self.assertEqual(hit["response_body"], '{"ok":true}')

        # 同一键重复写入不覆盖（保证首次响应原样回放）
        self.repo.put_idempotent("POST /x", "key-1", "hash-b", 500, '{"ok":false}')
        hit = self.repo.get_idempotent("POST /x", "key-1")
        self.assertEqual(hit["request_hash"], "hash-a")

    def test_idempotency_written_in_same_transaction(self) -> None:
        session = new_session()
        self.repo.create_session(session)
        session.messages.append(Msg(seq=2, role=Role.ARCHITECT, kind=Kind.STATEMENT, text="b"))
        self.repo.save_after_decision(
            session,
            1,
            action="post_message",
            decision_code="ok",
            idempotency=("POST /api/v1/discussions/s-1/messages", "k-1", "h-1", 200, '{"ok":true}'),
        )
        hit = self.repo.get_idempotent("POST /api/v1/discussions/s-1/messages", "k-1")
        self.assertIsNotNone(hit)
        events = self.repo.list_events("s-1")
        self.assertEqual(events[0]["action"], "post_message")

    # ------------------------------------------------------------------ 调用尝试

    def test_attempts_and_interruption(self) -> None:
        self.repo.create_session(new_session())
        self.repo.record_attempt(
            session_id="s-1",
            logical_task_id="t-1",
            attempt_no=1,
            role="architect",
            task_kind="statement",
            snapshot_ref="snap-v1",
            input_version=1,
        )
        self.assertEqual(self.repo.list_attempts("s-1")[0]["status"], "QUEUED")

        self.repo.finish_attempt(
            session_id="s-1",
            logical_task_id="t-1",
            attempt_no=1,
            status="SUCCEEDED",
            usage={"model": "deepseek", "prompt_tokens": 10},
        )
        attempt = self.repo.list_attempts("s-1")[0]
        self.assertEqual(attempt["status"], "SUCCEEDED")
        self.assertEqual(attempt["usage"]["prompt_tokens"], 10)

        self.repo.record_attempt(
            session_id="s-1",
            logical_task_id="t-2",
            attempt_no=1,
            role="product",
            task_kind="statement",
            snapshot_ref="snap-v1",
            input_version=1,
            status="RUNNING",
        )
        self.assertEqual(self.repo.mark_interrupted_attempts(), 1)
        statuses = {a["logical_task_id"]: a["status"] for a in self.repo.list_attempts("s-1")}
        self.assertEqual(statuses["t-2"], "FAILED")
        self.assertEqual(statuses["t-1"], "SUCCEEDED")

    # ------------------------------------------------------------------ 草稿 / 评审 / 交接

    def test_draft_review_handoff_flow(self) -> None:
        self.repo.create_session(new_session())
        self.repo.save_draft(
            draft_id="d-1",
            session_id="s-1",
            version=1,
            input_version=1,
            content={"requirements": [{"id": "FR-01"}]},
            source_map={"FR-01": [1]},
            open_issue_ids=["I-1"],
        )
        self.repo.save_draft(
            draft_id="d-2",
            session_id="s-1",
            version=2,
            input_version=1,
            content={"requirements": [{"id": "FR-01"}]},
            parent_id="d-1",
        )
        self.repo.save_review(
            review_id="r-1", session_id="s-1", draft_id="d-2", input_version=1,
            verdict="pass", findings=[],
        )
        handoff = self.repo.record_handoff(
            session_id="s-1",
            draft_id="d-2",
            review_id="r-1",
            input_version=1,
            idempotency_key="k-9",
            content_hash="sha256:abc123",
            content_json='{"schema_version":"1.0"}',
        )
        self.assertEqual(handoff["handoff_status"], "confirmed")
        # 签收产物必须落库：正文与哈希是交接给 FlowStudio 的依据
        self.assertEqual(handoff["content_hash"], "sha256:abc123")
        self.assertEqual(json.loads(handoff["content_json"]), {"schema_version": "1.0"})

        history = self.repo.history("s-1")
        self.assertEqual([d["id"] for d in history["drafts"]], ["d-1", "d-2"])
        self.assertEqual([d["is_current"] for d in history["drafts"]], [0, 1])
        self.assertEqual(history["handoff"]["review_id"], "r-1")

        self.repo.mark_handoff_delivered("s-1", "spec-1")
        self.assertEqual(self.repo.get_handoff("s-1")["handoff_status"], "delivered")

    # ------------------------------------------------------------------ 签收原子性

    def test_handoff_written_together_with_state(self) -> None:
        """签收：状态推进 + 交接记录 + 幂等键必须同事务落库。"""
        session = new_session()
        self.repo.create_session(session)
        session.status = Status.COMPLETED
        session.messages.append(Msg(seq=2, role=Role.ARCHITECT, kind=Kind.STATEMENT, text="已签收"))
        body = '{"decision":{"ok":true},"handoff":{"status":"confirmed"}}'
        self.repo.save_after_decision(
            session,
            1,
            action="confirm",
            idempotency=("POST /confirm", "k-confirm", "h-confirm", 200, body),
            handoff={
                "draft_id": "d-2",
                "review_id": "r-1",
                "input_version": 1,
                "confirmed_at": "2026-11-23T00:00:00Z",
                "idempotency_key": "k-confirm",
            },
        )
        handoff = self.repo.get_handoff("s-1")
        self.assertEqual(handoff["draft_id"], "d-2")
        self.assertEqual(handoff["handoff_status"], "confirmed")
        hit = self.repo.get_idempotent("POST /confirm", "k-confirm")
        self.assertEqual(hit["response_body"], body)
        restored, meta = self.repo.load_state("s-1")
        self.assertEqual(meta["status"], "completed")
        self.assertEqual(meta["state_version"], 2)
        self.assertEqual(len(restored.messages), 2)

    # ------------------------------------------------------------------ 重启

    def test_state_survives_new_process(self) -> None:
        session = new_session()
        session.nested = {Role.ARCHITECT: {"I-1", "I-2"}}
        self.repo.create_session(session)
        session.messages.append(Msg(seq=2, role=Role.ARCHITECT, kind=Kind.STATEMENT, text="已在事务内校验"))
        self.repo.save_after_decision(session, 1)

        reopened = DiscussionRepository(self.db, dumper=codec.dumps, loader=lambda raw: codec.loads(raw, Sess))
        restored, meta = reopened.load_state("s-1")
        self.assertEqual(restored, session)
        self.assertEqual(meta["state_version"], 2)
        self.assertEqual(restored.nested[Role.ARCHITECT], {"I-1", "I-2"})
        self.assertEqual(len(reopened.list_messages_after("s-1", 0, 10)[0]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
