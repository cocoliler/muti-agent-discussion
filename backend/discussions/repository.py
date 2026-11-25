"""仓储层：SQLite 事务、seq 镜像、CAS 与幂等（实施方案 §6）。

设计要点：

* ``save_after_decision`` 是**唯一**改写会话与投影表的事务入口，对应设计文档
  "协调服务在单次事务中校验尝试身份和输入版本、追加发言、更新状态"。
* ``seq`` 由引擎生成，本层只做镜像：任何 SQL 都不出现 ``seq + 1``。
* 模型调用发生在事务之外，因此本层不感知模型，只落结果。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import time
from typing import Any, Iterator, Mapping, Sequence

from .codec import dump_message_row, dump_session, encode, load_session

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

MSG_COLUMNS = (
    "session_id, seq, role, kind, text, topic_id, task_id, reply_to_seq, "
    "source_seqs, item_refs, issue_ids, input_version, round_no, created_at, analysis"
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class NotFound(LookupError):
    """会话不存在。"""


class VersionConflict(RuntimeError):
    """乐观并发令牌过期。"""

    def __init__(self, session_id: str, expected: int, current: int) -> None:
        super().__init__(f"会话 {session_id} 的 state_version 期望 {expected}，当前 {current}")
        self.session_id = session_id
        self.expected = expected
        self.current = current


class DuplicateProjection(RuntimeError):
    """投影表与引擎序号不一致。"""


class IdempotencyRecorded(RuntimeError):
    """已有相同作用域和幂等键的结果。"""


class LeaseLost(RuntimeError):
    """另一个执行器已接管会话。"""


class StorageBusy(RuntimeError):
    """SQLite 写锁竞争超时，对应 503 STORAGE_BUSY（契约既有错误码）。"""


class DiscussionRepository:
    """单文件 SQLite 仓储。每次操作使用独立连接，避免跨线程共享。"""

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        dumper: Any = None,
        loader: Any = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        # 默认绑定引擎编解码；测试可注入替身以脱离引擎独立验证
        self._dump = dumper or dump_session
        self._load = loader or load_session

    # ------------------------------------------------------------------ 连接

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:  # database is locked
                raise StorageBusy(str(exc)) from exc
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                try:
                    conn.execute("COMMIT")
                except sqlite3.OperationalError as exc:
                    raise StorageBusy(str(exc)) from exc

    # ------------------------------------------------------------------ 迁移

    #: 已发布表结构上新增的列（CREATE TABLE IF NOT EXISTS 不会补列）。
    COLUMN_ADDITIONS: dict[str, dict[str, str]] = {
        "discussion_sessions": {"deleted_at": "TEXT"},
        "role_attempts": {"elapsed_seconds": "REAL"},
        "discussion_messages": {"analysis": "TEXT NOT NULL DEFAULT '{}'"},
        "spec_handoffs": {
            "spec_expected_revision": "INTEGER NOT NULL DEFAULT 0",
            "content_hash": "TEXT",
            "content_json": "TEXT",
        },
    }

    def init_schema(self) -> None:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.connect() as conn:
            conn.executescript(sql)
        with self._write() as conn:
            self._apply_column_additions(conn)
            conn.execute(
                "INSERT OR IGNORE INTO spec_handoff_versions"
                " SELECT session_id, draft_id, review_id, input_version, confirmed_at, content_hash, content_json"
                " FROM spec_handoffs"
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, utcnow()),
            )

    def _apply_column_additions(self, conn: sqlite3.Connection) -> None:
        for table, columns in self.COLUMN_ADDITIONS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    # ------------------------------------------------------------------ 会话

    def create_session(
        self,
        session: Any,
        *,
        config_snapshot: str | None = None,
        agent_mode: str = "off",
        idempotency: tuple[str, str, str, int, str] | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        with self._write() as conn:
            self._assert_new_idempotency(conn, idempotency)
            conn.execute(
                """
                INSERT INTO discussion_sessions
                  (id, project_id, status, input_version, round_no, last_seq, active_draft_id,
                   config_snapshot, session_state, codec_version, state_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    session.session_id,
                    session.project_id,
                    session.status.value,
                    session.input_version,
                    session.round_no,
                    session.last_seq,
                    getattr(getattr(session, "current_draft", None), "draft_id", None),
                    config_snapshot or "{}",
                    self._dump(session),
                    now,
                    now,
                ),
            )
            self._insert_messages(conn, session, after_seq=0, created_at=now)
            self._sync_derived(conn, session, now)
            self._queue_run(conn, session.session_id, agent_mode)
            if idempotency is not None:
                scope, key, request_hash, status_code, body = idempotency
                conn.execute(
                    "INSERT INTO idempotency_keys(scope, key, request_hash, status_code, response_body, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (scope, key, request_hash, status_code, body, now),
                )
        return self.load_meta(session.session_id)

    def list_discussions(self, limit: int, before: tuple[str, str] | None = None) -> list[dict[str, Any]]:
        boundary = "AND (s.updated_at, s.id) < (?, ?)" if before else ""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT s.id, s.project_id, s.status, s.round_no, s.updated_at,"
                " COALESCE(substr(m.text, 1, 120), '') AS title"
                " FROM discussion_sessions s"
                " LEFT JOIN discussion_messages m ON m.session_id = s.id AND m.seq = 1"
                " WHERE s.deleted_at IS NULL"
                f" {boundary} ORDER BY s.updated_at DESC, s.id DESC LIMIT ?",
                (*(before or ()), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_discussion(self, session_id: str) -> None:
        """Hide the discussion and revoke its execution lease in the same transaction."""
        now = utcnow()
        with self._write() as conn:
            gone = conn.execute(
                "UPDATE discussion_sessions SET deleted_at = ?, state_version = state_version + 1"
                " WHERE id = ? AND deleted_at IS NULL",
                (now, session_id),
            )
            if not gone.rowcount:
                if conn.execute("SELECT 1 FROM discussion_sessions WHERE id = ?", (session_id,)).fetchone() is None:
                    raise NotFound(session_id)
                return
            conn.execute("DELETE FROM discussion_runs WHERE session_id = ?", (session_id,))
            conn.execute(
                "UPDATE role_attempts SET status = 'CANCELLED', finished_at = ?"
                " WHERE session_id = ? AND status IN ('QUEUED', 'RUNNING')",
                (now, session_id),
            )

    def load_state(self, session_id: str) -> tuple[Any, dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT session_state, state_version, status, input_version, round_no, last_seq,"
                " active_draft_id, codec_version FROM discussion_sessions WHERE id = ? AND deleted_at IS NULL",
                (session_id,),
            ).fetchone()
        if row is None:
            raise NotFound(session_id)
        session = self._load(row["session_state"])
        return session, self._meta_from_row(row)

    def load_meta(self, session_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT state_version, status, input_version, round_no, last_seq,"
                " active_draft_id, codec_version FROM discussion_sessions WHERE id = ? AND deleted_at IS NULL",
                (session_id,),
            ).fetchone()
        if row is None:
            raise NotFound(session_id)
        return self._meta_from_row(row)

    @staticmethod
    def _meta_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "state_version": row["state_version"],
            "status": row["status"],
            "input_version": row["input_version"],
            "round_no": row["round_no"],
            "last_seq": row["last_seq"],
            "active_draft_id": row["active_draft_id"],
            "codec_version": row["codec_version"],
        }

    def save_after_decision(
        self,
        session: Any,
        expected_state_version: int,
        *,
        action: str | None = None,
        decision_code: str | None = None,
        request_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        idempotency: tuple[str, str, str, int, str] | None = None,
        handoff: Mapping[str, Any] | None = None,
        agent_mode: str = "off",
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        """CAS 更新会话 + 追加投影 + 审计 + 幂等键，单事务完成。"""
        now = utcnow()
        with self._write() as conn:
            self._check_lease(conn, session.session_id, lease_owner)
            self._assert_new_idempotency(conn, idempotency)
            cursor = conn.execute(
                """
                UPDATE discussion_sessions
                   SET status = ?, input_version = ?, round_no = ?, last_seq = ?,
                       active_draft_id = ?, session_state = ?, codec_version = 1,
                       state_version = state_version + 1, updated_at = ?
                 WHERE id = ? AND state_version = ? AND deleted_at IS NULL
                """,
                (
                    session.status.value,
                    session.input_version,
                    session.round_no,
                    session.last_seq,
                    getattr(getattr(session, "current_draft", None), "draft_id", None),
                    self._dump(session),
                    now,
                    session.session_id,
                    expected_state_version,
                ),
            )
            if cursor.rowcount != 1:
                current = conn.execute(
                    "SELECT state_version FROM discussion_sessions WHERE id = ?",
                    (session.session_id,),
                ).fetchone()
                if current is None:
                    raise NotFound(session.session_id)
                raise VersionConflict(session.session_id, expected_state_version, current["state_version"])

            high_water = self._high_water(conn, session.session_id)
            self._insert_messages(conn, session, after_seq=high_water, created_at=now)
            self._sync_derived(conn, session, now)

            self._queue_run(conn, session.session_id, agent_mode)

            if action is not None:
                conn.execute(
                    "INSERT INTO discussion_events(session_id, action, payload, decision_code, request_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        session.session_id,
                        action,
                        json.dumps(dict(payload or {}), ensure_ascii=False),
                        decision_code or "ok",
                        request_id,
                        now,
                    ),
                )
            if idempotency is not None:
                scope, key, request_hash, status_code, body = idempotency
                conn.execute(
                    "INSERT INTO idempotency_keys(scope, key, request_hash, status_code, response_body, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (scope, key, request_hash, status_code, body, now),
                )
            if handoff is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO spec_handoff_versions VALUES (?,?,?,?,?,?,?)",
                    (session.session_id, handoff["draft_id"], handoff["review_id"], handoff["input_version"],
                     handoff["confirmed_at"], handoff.get("content_hash"), handoff.get("content_json")),
                )
                # 签收与状态更新同事务，避免"已签收但会话未推进"的半提交
                conn.execute(
                    "INSERT INTO spec_handoffs(session_id, draft_id, review_id, input_version,"
                    " confirmed_at, handoff_status, spec_revision_id, spec_expected_revision,"
                    " content_hash, content_json, idempotency_key)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(session_id) DO UPDATE SET draft_id = excluded.draft_id,"
                    " review_id = excluded.review_id, input_version = excluded.input_version,"
                    " confirmed_at = excluded.confirmed_at, handoff_status = excluded.handoff_status,"
                    " spec_expected_revision = excluded.spec_expected_revision,"
                    " content_hash = excluded.content_hash,"
                    " content_json = excluded.content_json,"
                    " spec_revision_id = excluded.spec_revision_id",
                    (
                        session.session_id,
                        handoff["draft_id"],
                        handoff["review_id"],
                        handoff["input_version"],
                        handoff["confirmed_at"],
                        handoff.get("handoff_status", "confirmed"),
                        handoff.get("spec_revision_id"),
                        int(handoff.get("spec_expected_revision", 0) or 0),
                        handoff.get("content_hash"),
                        handoff.get("content_json"),
                        handoff.get("idempotency_key"),
                    ),
                )
            row = conn.execute(
                "SELECT state_version FROM discussion_sessions WHERE id = ?",
                (session.session_id,),
            ).fetchone()
        return {
            "state_version": row["state_version"],
            "status": session.status.value,
            "input_version": session.input_version,
            "round_no": session.round_no,
            "last_seq": session.last_seq,
            "active_draft_id": getattr(getattr(session, "current_draft", None), "draft_id", None),
            "codec_version": 1,
        }

    @staticmethod
    def _high_water(conn: sqlite3.Connection, session_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS seq FROM discussion_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["seq"])

    def _insert_messages(self, conn: sqlite3.Connection, session: Any, *, after_seq: int, created_at: str) -> int:
        pending = [m for m in session.messages if m.seq > after_seq]
        if not pending:
            return 0
        expected = list(range(after_seq + 1, after_seq + 1 + len(pending)))
        actual = [m.seq for m in pending]
        if actual != expected:
            raise DuplicateProjection(f"投影序号不连续：期望 {expected[:5]}…，实际 {actual[:5]}…")
        conn.executemany(
            f"INSERT INTO discussion_messages({MSG_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [dump_message_row(m, session.session_id, created_at) for m in pending],
        )
        return len(pending)

    def _sync_derived(self, conn: sqlite3.Connection, session: Any, now: str) -> None:
        """把引擎内存里的草稿与评审投影到表。

        设计文档 §5 要求持久化 "草稿与评审历史"；引擎把这些对象放在会话状态里，
        但历史查看、差异视图和签收导出都必须能按版本查询，因此每次事务内同步一次。
        行是不可变的按版本追加，``is_current`` 随引擎的当前草稿翻转。
        """
        current = getattr(session, "current_draft", None)
        current_id = getattr(current, "draft_id", None)
        for draft in getattr(session, "drafts", ()) or ():
            source_map = {
                "source_seqs": list(getattr(draft, "source_seqs", ()) or ()),
                "review_id": getattr(draft, "review_id", None),
            }
            conn.execute(
                "INSERT INTO draft_revisions(id, session_id, parent_id, version, input_version, content,"
                " source_map, open_issue_ids, is_current, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET content = excluded.content,"
                " source_map = excluded.source_map, open_issue_ids = excluded.open_issue_ids,"
                " is_current = excluded.is_current",
                (
                    draft.draft_id,
                    session.session_id,
                    getattr(draft, "parent_id", None),
                    draft.version,
                    draft.input_version,
                    json.dumps(dict(draft.content), ensure_ascii=False),
                    json.dumps(source_map, ensure_ascii=False),
                    json.dumps(list(getattr(draft, "open_issue_ids", ()) or ()), ensure_ascii=False),
                    1 if draft.draft_id == current_id else 0,
                    now,
                ),
            )
        for review in getattr(session, "reviews", ()) or ():
            verdict = getattr(review.verdict, "value", review.verdict)
            findings = [encode(item) for item in getattr(review, "findings", ()) or ()]
            conn.execute(
                "INSERT INTO review_results(id, session_id, draft_id, input_version, verdict, findings, created_at)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET verdict = excluded.verdict, findings = excluded.findings",
                (
                    review.review_id,
                    session.session_id,
                    review.draft_id,
                    review.input_version,
                    verdict,
                    json.dumps(findings, ensure_ascii=False),
                    now,
                ),
            )

    # ------------------------------------------------------------------ 消息

    def message_high_water(self, session_id: str) -> int:
        with self.connect() as conn:
            return self._high_water(conn, session_id)

    def list_messages_after(self, session_id: str, after_seq: int, limit: int) -> tuple[list[dict[str, Any]], bool]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT seq, role, kind, text, topic_id, task_id, reply_to_seq, source_seqs,"
                " item_refs, issue_ids, input_version, round_no, created_at, analysis"
                " FROM discussion_messages WHERE session_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (session_id, after_seq, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        return [self._message_row(row) for row in rows[:limit]], has_more

    @staticmethod
    def _message_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "seq": row["seq"],
            "role": row["role"],
            "kind": row["kind"],
            "text": row["text"],
            "topic_id": row["topic_id"],
            "task_id": row["task_id"],
            "reply_to_seq": row["reply_to_seq"],
            "source_seqs": json.loads(row["source_seqs"]),
            "item_refs": json.loads(row["item_refs"]),
            "issue_ids": json.loads(row["issue_ids"]),
            "input_version": row["input_version"],
            "round_no": row["round_no"],
            "created_at": row["created_at"],
            **json.loads(row["analysis"]),
        }

    @staticmethod
    def _queue_run(conn: sqlite3.Connection, session_id: str, mode: str) -> None:
        if mode not in {"simulation", "real"}:
            return
        conn.execute(
            "INSERT INTO discussion_runs(session_id, mode) VALUES (?, ?)"
            " ON CONFLICT(session_id) DO UPDATE SET generation = generation + 1,"
            " owner = CASE WHEN mode = excluded.mode THEN owner END,"
            " mode = excluded.mode",
            (session_id, mode),
        )

    def pending_runs(self, mode: str, limit: int | None = None) -> list[str]:
        with self.connect() as conn:
            return [row[0] for row in conn.execute(
                "SELECT session_id FROM discussion_runs WHERE mode = ? AND expires_at <= ? ORDER BY expires_at, session_id LIMIT ?",
                (mode, time(), -1 if limit is None else limit),
            )]

    def claim_run(self, session_id: str, mode: str, owner: str, ttl: float) -> int | None:
        with self._write() as conn:
            now = time()
            row = conn.execute(
                "UPDATE discussion_runs SET owner = ?, expires_at = ?"
                " WHERE session_id = ? AND mode = ? AND expires_at <= ? RETURNING generation",
                (owner, now + ttl, session_id, mode, now),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE role_attempts SET status = 'FAILED', error = ?, finished_at = ?"
                " WHERE session_id = ? AND status IN ('QUEUED', 'RUNNING')",
                (json.dumps({"code": "PROCESS_INTERRUPTED"}), utcnow(), session_id),
            )
            return row["generation"]

    def renew_run(self, session_id: str, owner: str, ttl: float) -> None:
        with self._write() as conn:
            self._check_lease(conn, session_id, owner)
            conn.execute("UPDATE discussion_runs SET expires_at = ? WHERE session_id = ?", (time() + ttl, session_id))

    def release_run(self, session_id: str, owner: str, generation: int, *, finished: bool) -> None:
        with self._write() as conn:
            if finished:
                conn.execute(
                    "DELETE FROM discussion_runs WHERE session_id = ? AND owner = ? AND generation = ?",
                    (session_id, owner, generation),
                )
            conn.execute(
                "UPDATE discussion_runs SET owner = NULL, expires_at = 0 WHERE session_id = ? AND owner = ?",
                (session_id, owner),
            )

    @staticmethod
    def _check_lease(conn: sqlite3.Connection, session_id: str, owner: str | None) -> None:
        if owner is not None and not conn.execute(
            "SELECT 1 FROM discussion_runs WHERE session_id = ? AND owner = ? AND expires_at > ?",
            (session_id, owner, time()),
        ).fetchone():
            raise LeaseLost(session_id)

    # ------------------------------------------------------------------ 调用尝试

    def record_attempt(
        self,
        *,
        session_id: str,
        logical_task_id: str,
        attempt_no: int,
        role: str,
        task_kind: str,
        snapshot_ref: str,
        input_version: int,
        status: str = "QUEUED",
        lease_owner: str | None = None,
    ) -> None:
        with self._write() as conn:
            self._check_lease(conn, session_id, lease_owner)
            conn.execute(
                "INSERT INTO role_attempts(session_id, logical_task_id, attempt_no, role, task_kind,"
                " snapshot_ref, input_version, status, started_at) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(session_id, logical_task_id, attempt_no) DO UPDATE SET"
                " status = excluded.status, started_at = excluded.started_at",
                (
                    session_id,
                    logical_task_id,
                    attempt_no,
                    role,
                    task_kind,
                    snapshot_ref,
                    input_version,
                    status,
                    utcnow(),
                ),
            )

    def finish_attempt(
        self,
        *,
        session_id: str,
        logical_task_id: str,
        attempt_no: int,
        status: str,
        usage: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
        output: str | None = None,
        elapsed_seconds: float | None = None,
        lease_owner: str | None = None,
    ) -> None:
        with self._write() as conn:
            self._check_lease(conn, session_id, lease_owner)
            conn.execute(
                "UPDATE role_attempts SET status = ?, usage = ?, error = ?, output = ?, finished_at = ?, elapsed_seconds = ?"
                " WHERE session_id = ? AND logical_task_id = ? AND attempt_no = ?",
                (
                    status,
                    json.dumps(dict(usage), ensure_ascii=False) if usage else None,
                    json.dumps(dict(error), ensure_ascii=False) if error else None,
                    output,
                    utcnow(),
                    elapsed_seconds,
                    session_id,
                    logical_task_id,
                    attempt_no,
                ),
            )

    def latest_attempt(self, session_id: str, logical_task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT attempt_no, input_version, snapshot_ref, error FROM role_attempts"
                " WHERE session_id = ? AND logical_task_id = ? ORDER BY attempt_no DESC LIMIT 1",
                (session_id, logical_task_id),
            ).fetchone()
        return {**dict(row), "error": json.loads(row["error"]) if row["error"] else None} if row else None

    def list_attempts(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT logical_task_id, attempt_no, role, task_kind, snapshot_ref, input_version,"
                " status, usage, error, output, started_at, finished_at, elapsed_seconds FROM role_attempts"
                " WHERE session_id = ? ORDER BY logical_task_id, attempt_no",
                (session_id,),
            ).fetchall()
        return [
            {
                **{k: row[k] for k in row.keys()},
                "usage": json.loads(row["usage"]) if row["usage"] else None,
                "error": json.loads(row["error"]) if row["error"] else None,
            }
            for row in rows
        ]

    def mark_interrupted_attempts(self, reason: str = "PROCESS_INTERRUPTED") -> int:
        """进程启动时调用：未终结的调用置 FAILED，避免"已有结果"的假象。"""
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE role_attempts SET status = 'FAILED', error = ?, finished_at = ?"
                " WHERE status IN ('QUEUED', 'RUNNING')"
                " AND NOT EXISTS (SELECT 1 FROM discussion_runs WHERE discussion_runs.session_id = role_attempts.session_id)",
                (json.dumps({"code": reason, "message": "进程重启，调用结果不明"}, ensure_ascii=False), utcnow()),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------ 草稿 / 评审 / 交接

    def save_draft(
        self,
        *,
        draft_id: str,
        session_id: str,
        version: int,
        input_version: int,
        content: Mapping[str, Any],
        source_map: Mapping[str, Any] | None = None,
        open_issue_ids: Sequence[str] = (),
        parent_id: str | None = None,
        is_current: bool = True,
    ) -> None:
        with self._write() as conn:
            if is_current:
                conn.execute("UPDATE draft_revisions SET is_current = 0 WHERE session_id = ?", (session_id,))
            conn.execute(
                "INSERT OR REPLACE INTO draft_revisions(id, session_id, parent_id, version, input_version,"
                " content, source_map, open_issue_ids, is_current, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    draft_id,
                    session_id,
                    parent_id,
                    version,
                    input_version,
                    json.dumps(dict(content), ensure_ascii=False),
                    json.dumps(dict(source_map or {}), ensure_ascii=False),
                    json.dumps(list(open_issue_ids), ensure_ascii=False),
                    1 if is_current else 0,
                    utcnow(),
                ),
            )

    def save_review(
        self,
        *,
        review_id: str,
        session_id: str,
        draft_id: str,
        input_version: int,
        verdict: str,
        findings: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO review_results(id, session_id, draft_id, input_version, verdict,"
                " findings, created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    review_id,
                    session_id,
                    draft_id,
                    input_version,
                    verdict,
                    json.dumps([dict(f) for f in findings], ensure_ascii=False),
                    utcnow(),
                ),
            )

    def record_handoff(
        self,
        *,
        session_id: str,
        draft_id: str,
        review_id: str,
        input_version: int,
        idempotency_key: str | None = None,
        spec_expected_revision: int = 0,
        content_hash: str | None = None,
        content_json: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO spec_handoffs(session_id, draft_id, review_id, input_version, confirmed_at,"
                " handoff_status, spec_expected_revision, content_hash, content_json, idempotency_key)"
                " VALUES (?,?,?,?,?, 'confirmed', ?, ?, ?, ?)"
                " ON CONFLICT(session_id) DO UPDATE SET draft_id = excluded.draft_id,"
                " review_id = excluded.review_id, input_version = excluded.input_version,"
                " confirmed_at = excluded.confirmed_at, handoff_status = 'confirmed',"
                " spec_expected_revision = excluded.spec_expected_revision,"
                " content_hash = excluded.content_hash,"
                " content_json = excluded.content_json,"
                " idempotency_key = COALESCE(excluded.idempotency_key, spec_handoffs.idempotency_key)",
                (
                    session_id,
                    draft_id,
                    review_id,
                    input_version,
                    now,
                    spec_expected_revision,
                    content_hash,
                    content_json,
                    idempotency_key,
                ),
            )
        return self.get_handoff(session_id) or {}

    def get_handoff(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM spec_handoffs WHERE session_id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def get_signed_draft(self, session_id: str, draft_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM spec_handoff_versions WHERE session_id = ? AND draft_id = ?",
                (session_id, draft_id),
            ).fetchone()
            if row is None:
                row = conn.execute("SELECT * FROM spec_handoffs WHERE session_id = ? AND draft_id = ?",
                                   (session_id, draft_id)).fetchone()
        return dict(row) if row else None

    def signed_draft_ids(self, session_id: str) -> set[str]:
        with self.connect() as conn:
            return {row[0] for row in conn.execute(
                "SELECT draft_id FROM spec_handoff_versions WHERE session_id = ?"
                " UNION SELECT draft_id FROM spec_handoffs WHERE session_id = ?", (session_id, session_id),
            )}

    def mark_handoff_delivered(self, session_id: str, spec_revision_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE spec_handoffs SET handoff_status = 'delivered', spec_revision_id = ? WHERE session_id = ?",
                (spec_revision_id, session_id),
            )

    # ------------------------------------------------------------------ 历史

    def history(self, session_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            drafts = conn.execute(
                "SELECT id, parent_id, version, input_version, content, source_map, open_issue_ids,"
                " is_current, created_at FROM draft_revisions WHERE session_id = ? ORDER BY version",
                (session_id,),
            ).fetchall()
            reviews = conn.execute(
                "SELECT id, draft_id, input_version, verdict, findings, created_at FROM review_results"
                " WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return {
            "drafts": [
                {
                    **{k: row[k] for k in ("id", "parent_id", "version", "input_version", "is_current", "created_at")},
                    "content": json.loads(row["content"]),
                    "source_map": json.loads(row["source_map"]),
                    "open_issue_ids": json.loads(row["open_issue_ids"]),
                }
                for row in drafts
            ],
            "reviews": [
                {
                    **{k: row[k] for k in ("id", "draft_id", "input_version", "verdict", "created_at")},
                    "findings": json.loads(row["findings"]),
                }
                for row in reviews
            ],
            "attempts": self.list_attempts(session_id),
            "handoff": self.get_handoff(session_id),
        }

    # ------------------------------------------------------------------ 幂等

    @staticmethod
    def _assert_new_idempotency(conn: sqlite3.Connection, record: tuple | None) -> None:
        if record is not None and conn.execute(
            "SELECT 1 FROM idempotency_keys WHERE scope = ? AND key = ?", record[:2],
        ).fetchone():
            raise IdempotencyRecorded

    def get_idempotent(self, scope: str, key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT request_hash, status_code, response_body FROM idempotency_keys WHERE scope = ? AND key = ?",
                (scope, key),
            ).fetchone()
        return dict(row) if row else None

    def put_idempotent(self, scope: str, key: str, request_hash: str, status_code: int, body: str) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO idempotency_keys(scope, key, request_hash, status_code, response_body, created_at)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT(scope, key) DO NOTHING",
                (scope, key, request_hash, status_code, body, utcnow()),
            )

    def list_events(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT action, payload, decision_code, request_id, created_at FROM discussion_events"
                " WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [
            {
                "action": row["action"],
                "payload": json.loads(row["payload"]) if row["payload"] else None,
                "decision_code": row["decision_code"],
                "request_id": row["request_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
