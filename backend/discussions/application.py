"""应用服务：把引擎的决策接到仓储上（实施方案 §3、§9）。

职责边界：

* 读请求：只读仓储，**不触发任何模型调用**（刷新恢复路径）。
* 写请求：幂等键 → 读状态 → 校验 state_version → 调引擎 → 单事务落库。
* 模型调用由成员 2 的 runner 在事务之外完成，本层只接收其结果信封。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .codec import encode
from .errors import ApiError, decision_error_code, decision_status
from .repository import DiscussionRepository, NotFound, VersionConflict, utcnow


class DiscussionService:
    def __init__(
        self,
        repository: DiscussionRepository,
        engine: Any | None = None,
        models: Any | None = None,
        *,
        export_enabled: bool = False,
    ) -> None:
        self.repo = repository
        #: 导出路由是否已登记契约（决定 confirm 是否返回 export_url）
        self.export_enabled = export_enabled
        if engine is None or models is None:
            from discussion_orchestration import (  # 惰性导入：未装引擎时本模块仍可导入
                Agenda,
                DiscussionEngine,
                DiscussionLimits,
                HostProposal,
                ReviewResult,
                SpeakerOutput,
            )

            engine = engine or DiscussionEngine()
            models = {
                "Agenda": Agenda,
                "DiscussionLimits": DiscussionLimits,
                "HostProposal": HostProposal,
                "ReviewResult": ReviewResult,
                "SpeakerOutput": SpeakerOutput,
            }
        self.engine = engine
        self.models = models

    # ------------------------------------------------------------------ 读

    def create(
        self,
        project_id: str,
        idea: str,
        limits: Mapping[str, Any] | None,
        *,
        idempotency_key: str | None = None,
        request_hash: str = "",
        scope: str = "",
        request_id: str = "",
    ) -> tuple[int, dict[str, Any]]:
        from uuid import uuid4

        if idempotency_key:
            replayed = self._replay(scope, idempotency_key, request_hash)
            if replayed is not None:
                return replayed

        kwargs: dict[str, Any] = {}
        if limits:
            kwargs["limits"] = self.models["DiscussionLimits"](**dict(limits))
        session = self.engine.create_session(
            session_id=str(uuid4()),
            project_id=project_id,
            idea=idea,
        )
        # 引擎的 create_session 只接受三个关键字参数；自定义额度在建会话后挂到会话上
        if "limits" in kwargs:
            session.limits = kwargs["limits"]
        body = self._render(session, {"state_version": 1})
        self.repo.create_session(
            session,
            idempotency=(
                (scope, idempotency_key, request_hash, 201, json.dumps(body, ensure_ascii=False))
                if idempotency_key
                else None
            ),
        )
        return 201, body

    def view(self, session_id: str) -> dict[str, Any]:
        session, meta = self._load(session_id)
        return self._render(session, meta)

    def messages(self, session_id: str, after_seq: int, limit: int) -> dict[str, Any]:
        meta = self._meta(session_id)
        items, has_more = self.repo.list_messages_after(session_id, after_seq, limit)
        tail = items[-1]["seq"] if items else after_seq
        return {
            "items": items,
            "has_more": has_more,
            "last_seq": meta["last_seq"],
            # 不透明游标：契约列表统一用 next_cursor:null|string
            "next_cursor": str(tail) if has_more else None,
        }

    def history(self, session_id: str) -> dict[str, Any]:
        self._meta(session_id)
        return self.repo.history(session_id)

    def export_content(self, session_id: str) -> dict[str, Any]:
        handoff = self.repo.get_handoff(session_id)
        if not handoff:
            raise ApiError("VALIDATION_FAILED", "尚未签收，无法导出", status=409)
        raw = handoff.get("content_json")
        if not raw:
            raise ApiError("VALIDATION_FAILED", "签收记录缺少规格正文，无法导出", status=409)
        try:
            content = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError("VALIDATION_FAILED", "签收正文损坏，无法导出", status=409) from exc
        return {
            "content": content,
            "draft_id": handoff.get("draft_id"),
            "content_hash": handoff.get("content_hash"),
            "handoff": handoff,
        }

    # ------------------------------------------------------------------ 写

    def post_message(
        self,
        session_id: str,
        *,
        text: str,
        reply_to_seq: int | None,
        substantive: bool,
        expected_state_version: int,
        input_version: int | None = None,
        idempotency_key: str | None,
        request_hash: str,
        scope: str,
        request_id: str = "",
    ) -> tuple[int, dict[str, Any]]:
        if idempotency_key:
            replayed = self._replay(scope, idempotency_key, request_hash)
            if replayed is not None:
                return replayed

        session, meta = self._load(session_id)
        self._check_version(session_id, expected_state_version, meta)
        # §6：接收上下文版本；旧版本补充不得推进当前会话
        if input_version is not None and input_version != session.input_version:
            raise ApiError(
                "REVISION_CONFLICT",
                "补充内容基于已过期的上下文版本，请刷新后重试",
                status=409,
                detail={"input_version": session.input_version, "expected_input_version": input_version},
            )
        if reply_to_seq is not None:
            self._assert_local_reference(session_id, [reply_to_seq], meta)

        decision = self.engine.note_user_input(
            session, text, reply_to_seq=reply_to_seq, substantive=substantive
        )
        return self._commit(
            session,
            meta,
            decision,
            action="post_message",
            payload={"text_len": len(text), "reply_to_seq": reply_to_seq, "substantive": substantive},
            request_id=request_id,
            idempotency=(scope, idempotency_key, request_hash) if idempotency_key else None,
        )

    def act(
        self,
        session_id: str,
        *,
        action: str,
        payload: Mapping[str, Any],
        expected_state_version: int,
        idempotency_key: str | None,
        request_hash: str,
        scope: str,
        request_id: str = "",
    ) -> tuple[int, dict[str, Any]]:
        if idempotency_key:
            replayed = self._replay(scope, idempotency_key, request_hash)
            if replayed is not None:
                return replayed

        session, meta = self._load(session_id)
        self._check_version(session_id, expected_state_version, meta)
        decision = self._dispatch(session, action, dict(payload))
        return self._commit(
            session,
            meta,
            decision,
            action=action,
            payload=payload,
            request_id=request_id,
            idempotency=(scope, idempotency_key, request_hash) if idempotency_key else None,
        )

    def confirm(
        self,
        session_id: str,
        *,
        draft_id: str,
        input_version: int,
        expected_state_version: int,
        spec_expected_revision: int = 0,
        idempotency_key: str | None,
        request_hash: str,
        scope: str,
        request_id: str = "",
    ) -> tuple[int, dict[str, Any]]:
        if idempotency_key:
            replayed = self._replay(scope, idempotency_key, request_hash)
            if replayed is not None:
                return replayed

        session, meta = self._load(session_id)
        self._check_version(session_id, expected_state_version, meta)

        # 签收门禁以 spec_handoff.sign_off 为权威（纯读，不改状态）。
        # 先判门禁再调引擎：否则引擎已推进、交接却说不可签收，就成了半提交。
        signoff = self._sign_off(session, draft_id=draft_id, input_version=input_version)
        if not signoff.ok:
            return 200, self._signoff_rejected(session, meta, signoff)

        decision = self.engine.confirm(session, draft_id=draft_id, input_version=input_version)

        if not decision.ok:
            return self._reject(decision)

        record = signoff.handoff
        if record is None:  # pragma: no cover - sign_off 成功必然带交接记录
            raise ApiError("VALIDATION_FAILED", "签收结果缺少交接记录", status=409)

        content_hash = getattr(record, "content_hash", None)
        content_json = json.dumps(dict(record.content), ensure_ascii=False)
        handoff = {
            "draft_id": draft_id,
            "review_id": record.review_id,
            "input_version": input_version,
            "confirmed_at": getattr(record, "confirmed_at", None) or utcnow(),
            "handoff_status": "confirmed",
            "spec_expected_revision": spec_expected_revision,
            "content_hash": content_hash,
            "content_json": content_json,
            "idempotency_key": idempotency_key,
        }
        body = {
            "decision": self._decision_view(decision),
            "handoff": {
                "status": "confirmed",
                "draft_id": draft_id,
                "review_id": record.review_id,
                "input_version": input_version,
                "spec_expected_revision": spec_expected_revision,
                "content_hash": content_hash,
                "confirmed_at": handoff["confirmed_at"],
                "spec_revision_id": None,
                # 导出路由属第 7 个候选操作，未登记契约前默认不暴露，避免漂移检查拦截
                "export_url": (
                    f"/api/v1/discussions/{session_id}/export" if self.export_enabled else None
                ),
            },
            "session": self._render(session, {**meta, "state_version": meta["state_version"] + 1}),
        }
        # 状态推进、签收记录、幂等键三者同事务
        self.repo.save_after_decision(
            session,
            expected_state_version,
            action="confirm",
            decision_code="ok",
            request_id=request_id,
            payload={"draft_id": draft_id, "input_version": input_version},
            idempotency=(
                (scope, idempotency_key, request_hash, 200, json.dumps(body, ensure_ascii=False))
                if idempotency_key
                else None
            ),
            handoff=handoff,
        )
        return 200, body

    # ------------------------------------------------------------------ 内部

    def _load(self, session_id: str) -> tuple[Any, dict[str, Any]]:
        try:
            return self.repo.load_state(session_id)
        except NotFound as exc:
            raise ApiError("NOT_FOUND", f"找不到讨论会话 {session_id}", status=404) from exc

    def _meta(self, session_id: str) -> dict[str, Any]:
        try:
            return self.repo.load_meta(session_id)
        except NotFound as exc:
            raise ApiError("NOT_FOUND", f"找不到讨论会话 {session_id}", status=404) from exc

    @staticmethod
    def _check_version(session_id: str, expected: int, meta: Mapping[str, Any]) -> None:
        if meta["state_version"] != expected:
            raise ApiError(
                "REVISION_CONFLICT",
                "会话已被其他写入更新，请刷新后重试",
                status=409,
                detail={"state_version": meta["state_version"], "expected_state_version": expected},
            )

    def _assert_local_reference(self, session_id: str, seqs: list[int], meta: Mapping[str, Any]) -> None:
        """引用必须指向本会话已提交消息（跨会话引用在此处被拒）。"""
        if not seqs:
            return
        last = meta["last_seq"]
        invalid = [seq for seq in seqs if seq < 1 or seq > last]
        if invalid:
            raise ApiError(
                "VALIDATION_FAILED",
                f"引用的消息序号不属于本会话或尚未提交：{invalid}",
                status=422,
                issues=[{"path": "/reply_to_seq", "message": f"无效序号 {seq}"} for seq in invalid],
            )

    def _dispatch(self, session: Any, action: str, payload: dict[str, Any]) -> Any:
        engine = self.engine
        handler = {
            "pause": lambda: engine.pause(session, payload.get("reason", "")),
            "resume": lambda: engine.resume(session),
            "cancel": lambda: engine.cancel(session, payload.get("reason", "")),
            "require_changes": lambda: engine.require_changes(session, payload.get("reason", "")),
            "extend_quota": lambda: engine.extend_quota(
                session,
                revisions=int(payload.get("revisions", 0)),
                directed=int(payload.get("directed", 0)),
                rounds=int(payload.get("rounds", 0)),
                reason=payload.get("reason", ""),
            ),
            "abandon_task": lambda: engine.abandon_task(
                session, payload.get("task_id", ""), payload.get("reason", "")
            ),
            "resolve_issues": lambda: engine.resolve_issue_by_user(
                session, tuple(payload.get("issue_ids", ())), payload.get("note", "")
            ),
            "submit_output": lambda: engine.submit_output(
                session, self.models["SpeakerOutput"](**payload)
            ),
            "submit_review": lambda: engine.submit_review(
                session, self.models["ReviewResult"](**payload)
            ),
            "submit_agenda": lambda: engine.submit_agenda(
                session, self.models["Agenda"](**payload)
            ),
            "propose": lambda: engine.propose(
                session, self.models["HostProposal"](**payload)
            ),
        }
        if action == "retry":
            # 重试由成员 2 的 runner 执行；本层只把待重试任务原样返回，用于统一审计。
            task_ids = payload.get("task_ids") or []
            tasks = tuple(t for tid, t in getattr(session, "tasks", {}).items() if not task_ids or tid in task_ids)
            return _RetryPlan(tasks=tasks)
        if action not in handler:
            raise ApiError("VALIDATION_FAILED", f"不支持的动作 {action}", status=422)
        return handler[action]()

    def _commit(
        self,
        session: Any,
        meta: Mapping[str, Any],
        decision: Any,
        *,
        action: str,
        payload: Mapping[str, Any],
        request_id: str,
        idempotency: tuple[str, str, str] | None,
    ) -> tuple[int, dict[str, Any]]:
        if not decision.ok:
            return self._reject(decision)

        previous_last_seq = meta["last_seq"]
        new_messages = [m for m in getattr(session, "messages", ()) if m.seq > previous_last_seq]
        body = {
            "decision": self._decision_view(decision),
            "session": self._render(session, {**meta, "state_version": meta["state_version"] + 1}),
            "new_messages": [self._message_view(m) for m in new_messages],
            "tasks": [self._task_view(t) for t in getattr(decision, "tasks", ())],
        }
        key_tuple = None
        if idempotency is not None:
            scope, key, request_hash = idempotency
            key_tuple = (scope, key, request_hash, 200, json.dumps(body, ensure_ascii=False))
        self.repo.save_after_decision(
            session,
            meta["state_version"],
            action=action,
            decision_code=self._code(decision),
            request_id=request_id,
            payload=payload,
            idempotency=key_tuple,
        )
        return 200, body

    def _reject(self, decision: Any) -> tuple[int, dict[str, Any]]:
        code = self._code(decision)
        status = decision_status(code)
        envelope = {"decision": self._decision_view(decision), "session": None}
        if status != 200:
            error_code = decision_error_code(code) or "VALIDATION_FAILED"
            raise ApiError(
                error_code,
                getattr(decision, "message", "") or code,
                status=status,
                detail={"decision": envelope["decision"]},
            )
        return 200, envelope

    def _sign_off(self, session: Any, *, draft_id: str, input_version: int) -> Any:
        """调用成员 5 的签收门禁。纯读：只回答"能不能签"，不推进状态。"""
        try:
            from spec_handoff import sign_off
        except ImportError as exc:  # pragma: no cover - 仓库内总存在
            raise ApiError(
                "VALIDATION_FAILED",
                "缺少 spec_handoff 模块，无法校验签收条件",
                status=500,
            ) from exc
        return sign_off(session, draft_id=draft_id, input_version=input_version)

    def _signoff_rejected(self, session: Any, meta: Mapping[str, Any], result: Any) -> dict[str, Any]:
        """签收门禁拒绝：200 + decision.ok=false，附可定位的 findings（不占用幂等键）。"""
        findings = [item.as_dict() for item in getattr(result, "findings", ())]
        first = findings[0] if findings else {}
        return {
            "decision": {
                "ok": False,
                "code": first.get("code") or "VALIDATION_FAILED",
                "message": first.get("message") or "签收校验未通过",
                "waiting_for": None,
                "seq": None,
            },
            "session": self._render(session, meta),
            "result": {"findings": findings},
        }

    def _replay(self, scope: str, key: str, request_hash: str) -> tuple[int, dict[str, Any]] | None:
        row = self.repo.get_idempotent(scope, key)
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ApiError("IDEMPOTENCY_CONFLICT", "同一 Idempotency-Key 携带了不同请求体", status=409)
        try:
            body = json.loads(row["response_body"] or "{}")
        except json.JSONDecodeError:
            body = {}
        return int(row["status_code"] or 200), body

    @staticmethod
    def _code(decision: Any) -> str:
        code = getattr(decision, "code", "ok")
        return getattr(code, "value", str(code))

    @classmethod
    def _decision_view(cls, decision: Any) -> dict[str, Any]:
        waiting = getattr(decision, "waiting_for", None)
        return {
            "ok": bool(getattr(decision, "ok", False)),
            "code": cls._code(decision),
            "message": getattr(decision, "message", "") or "",
            "waiting_for": getattr(waiting, "value", waiting),
            "seq": getattr(decision, "seq", None),
        }

    @staticmethod
    def _message_view(message: Any) -> dict[str, Any]:
        return encode(message)

    @staticmethod
    def _task_view(task: Any) -> dict[str, Any]:
        return encode(task)

    def _render(self, session: Any, meta: Mapping[str, Any]) -> dict[str, Any]:
        draft = getattr(session, "current_draft", None)
        review_verdict = None
        if draft is not None:
            for item in reversed(getattr(session, "reviews", ())):
                if item.draft_id == draft.draft_id:
                    review_verdict = getattr(item.verdict, "value", item.verdict)
                    break

        limits = session.limits
        budget = {
            "rounds_used": session.round_no,
            "rounds_left": max(0, limits.max_total_rounds - session.round_no),
            "directed_used": session.directed_rounds,
            "directed_left": max(0, limits.max_directed_rounds - session.directed_rounds),
            "revisions_used": session.revision_rounds,
            "revisions_left": max(0, limits.max_revision_rounds - session.revision_rounds),
            "dispatch_units_used": session.dispatch_units,
            "dispatch_units_left": max(0, limits.max_dispatch_units - session.dispatch_units),
        }
        return {
            "id": session.session_id,
            "project_id": session.project_id,
            "status": session.status.value,
            "input_version": session.input_version,
            "round_no": session.round_no,
            "round_kind": getattr(session.round_kind, "value", session.round_kind),
            "phase": getattr(session.phase, "value", session.phase),
            "topic_id": session.topic_id,
            "waiting_for": getattr(session.waiting_for, "value", session.waiting_for),
            "last_seq": session.last_seq,
            "state_version": meta["state_version"],
            "participants": self._participants(session),
            "active_draft": (
                {
                    "draft_id": draft.draft_id,
                    "version": draft.version,
                    "input_version": draft.input_version,
                    "open_issue_ids": list(draft.open_issue_ids),
                    "review_verdict": review_verdict,
                    "content": dict(draft.content),
                }
                if draft is not None
                else None
            ),
            # §6 要求返回"草稿、评审"：历史以版本摘要呈现，正文在 active_draft
            "drafts": [
                {
                    "draft_id": item.draft_id,
                    "version": item.version,
                    "input_version": item.input_version,
                    "current": item.draft_id == getattr(draft, "draft_id", None),
                    "open_issue_ids": list(item.open_issue_ids),
                }
                for item in getattr(session, "drafts", ()) or ()
            ],
            "reviews": [
                {
                    "review_id": item.review_id,
                    "draft_id": item.draft_id,
                    "input_version": item.input_version,
                    "verdict": getattr(item.verdict, "value", item.verdict),
                    "findings": [encode(finding) for finding in getattr(item, "findings", ()) or ()],
                }
                for item in getattr(session, "reviews", ()) or ()
            ],
            "open_issues": [encode(issue) for issue in session.open_issues()],
            "budget": budget,
        }

    def _participants(self, session: Any) -> list[dict[str, Any]]:
        """参与者状态：调用尝试 > 本轮已发言 > 已派发待执行。"""
        latest: dict[str, dict[str, Any]] = {}
        for attempt in self.repo.list_attempts(session.session_id):
            role = attempt["role"]
            if role not in latest or attempt["attempt_no"] >= latest[role]["attempt_no"]:
                latest[role] = {
                    "role": role,
                    "status": str(attempt["status"]).lower(),
                    "attempt_no": attempt["attempt_no"],
                }

        done = {
            message.role.value
            for message in session.messages
            if message.round_no == session.round_no
        }
        queued = {
            getattr(task.role, "value", str(task.role))
            for task in (getattr(session, "tasks", {}) or {}).values()
        }
        roles = sorted(set(latest) | done | queued)
        return [
            latest.get(role)
            or {
                "role": role,
                "status": "done" if role in done else "queued",
                "attempt_no": None,
            }
            for role in roles
        ]


class _RetryCode(str, Enum):
    OK = "ok"


@dataclass
class _RetryPlan:
    """``retry`` 动作的最小决策对象，字段与引擎 ``Decision`` 对齐。"""

    tasks: tuple[Any, ...] = ()
    ok: bool = True
    code: _RetryCode = _RetryCode.OK
    message: str = ""
    seq: int | None = None
    waiting_for: Any = None
