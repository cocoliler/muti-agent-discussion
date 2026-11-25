"""应用服务：把引擎的决策接到仓储上（实施方案 §3、§9）。

职责边界：

* 读请求：只读仓储，**不触发任何模型调用**（刷新恢复路径）。
* 写请求：幂等键 → 读状态 → 校验 state_version → 调引擎 → 单事务落库。
* 自动角色执行在事务之外完成，结果通过相同的版本校验与编排动作写回。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from discussion_orchestration.models import ClarificationAnswer
from discussion_orchestration.scoring import rank_options

from .codec import CodecError, decode, encode
from .errors import ApiError, decision_error_code, decision_status
from .repository import IdempotencyRecorded, DiscussionRepository, NotFound, VersionConflict, utcnow


def _configured_limit(name: str, default: int | None) -> int | None:
    """轮次硬上限可由环境变量覆盖，非法值回落到默认。"""

    try:
        value = int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default
    return value if 1 <= value <= 50 else default


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
        self.agent_mode = "off"
        #: 导出路由是否开启（决定 confirm 是否返回 export_url）
        self.export_enabled = export_enabled
        if engine is None or models is None:
            from discussion_orchestration import (  # 惰性导入：未装引擎时本模块仍可导入
                Agenda,
                DiscussionEngine,
                HostProposal,
                ReviewResult,
                SpeakerOutput,
            )

            engine = engine or DiscussionEngine()
            models = {
                "Agenda": Agenda,
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

        session = self.engine.create_session(
            session_id=str(uuid4()),
            project_id=project_id,
            idea=idea,
        )
        session.max_directed_rounds = _configured_limit(
            "DISCUSSION_MAX_DIRECTED_ROUNDS", getattr(session, "max_directed_rounds", None)
        )
        session.max_revision_rounds = _configured_limit(
            "DISCUSSION_MAX_REVISION_ROUNDS", getattr(session, "max_revision_rounds", None)
        )
        body = self._render(session, {"state_version": 1})
        try:
            self.repo.create_session(
                session,
                agent_mode=self.agent_mode,
                idempotency=(
                    (scope, idempotency_key, request_hash, 201, json.dumps(body, ensure_ascii=False))
                    if idempotency_key
                    else None
                ),
            )
        except IdempotencyRecorded:
            replayed = self._replay(scope, idempotency_key, request_hash)
            if replayed is not None:
                return replayed
            raise
        return 201, body

    def delete(self, session_id: str) -> None:
        try:
            self.repo.delete_discussion(session_id)
        except NotFound as exc:
            raise ApiError("NOT_FOUND", "找不到讨论会话", status=404) from exc

    def discussions(self, limit: int, before: tuple[str, str] | None) -> dict[str, Any]:
        rows = self.repo.list_discussions(limit + 1, before)
        page = rows[:limit]
        cursor = f"{page[-1]['updated_at']}|{page[-1]['id']}" if len(rows) > limit else None
        for row in page:
            row["title"] = " ".join(row["title"].split()) or "未命名讨论"
        return {"items": page, "next_cursor": cursor}

    def draft(self, session_id: str, draft_id: str) -> dict[str, Any]:
        session, _ = self._load(session_id)
        draft = self._find_draft(session, draft_id)
        return self._draft_view(session, draft)

    @staticmethod
    def _find_draft(session: Any, draft_id: str) -> Any:
        draft = next((item for item in getattr(session, "drafts", ()) if item.draft_id == draft_id), None)
        if draft is None:
            raise ApiError("NOT_FOUND", f"找不到草稿版本 {draft_id}", status=404)
        return draft

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
            # 不透明游标：列表统一返回 next_cursor:null|string
            "next_cursor": str(tail) if has_more else None,
        }

    def history(self, session_id: str) -> dict[str, Any]:
        self._meta(session_id)
        return self.repo.history(session_id)

    def export_content(self, session_id: str, draft_id: str | None = None) -> dict[str, Any]:
        self._meta(session_id)
        if draft_id is not None:
            handoff = self.repo.get_signed_draft(session_id, draft_id)
            if not handoff or not handoff["content_json"]:
                return self._export_draft(session_id, draft_id)
        else:
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

    def _export_draft(self, session_id: str, draft_id: str) -> dict[str, Any]:
        """把任意草稿版本转成 FlowStudio 正文，供前端未签收时先导出排查。

        草稿里的 `source_seqs`、`covers`、`pain_points`、`risks`、
        `removed_requirement_ids` 只用于追溯，`DraftSpec.to_content()` 会把它们剔除，
        得到与 FlowStudio 导入契约同形的正文；结构不合格由导出层返回 409。
        """

        from spec_handoff import DraftSpec

        session, _ = self._load(session_id)
        draft = self._find_draft(session, draft_id)
        return {
            "content": DraftSpec.parse(draft.content).to_content(),
            "draft_id": draft.draft_id,
            "draft_version": draft.version,
            "content_hash": None,
            "handoff": None,
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
        clarification_answers: tuple[dict[str, Any], ...] = (),
        replaces_answer_seq: int | None = None,
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
        replayed = self._check_version(
            session_id, expected_state_version, meta,
            replay=(scope, idempotency_key, request_hash) if idempotency_key else None,
        )
        if replayed is not None:
            return replayed
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

        clarification = {}
        if clarification_answers:
            clarification["clarification_answers"] = tuple(
                ClarificationAnswer(answer["question"], answer["answer"], tuple(answer["options"]))
                for answer in clarification_answers)
        if replaces_answer_seq is not None:
            self._assert_local_reference(session_id, [replaces_answer_seq], meta)
            clarification["replaces_answer_seq"] = replaces_answer_seq
        decision = self.engine.note_user_input(session, text, reply_to_seq=reply_to_seq, substantive=substantive, **clarification)
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
        lease_owner: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if idempotency_key:
            replayed = self._replay(scope, idempotency_key, request_hash)
            if replayed is not None:
                return replayed

        session, meta = self._load(session_id)
        replayed = self._check_version(
            session_id, expected_state_version, meta,
            replay=(scope, idempotency_key, request_hash) if idempotency_key else None,
        )
        if replayed is not None:
            return replayed
        decision = self._dispatch(session, action, dict(payload))
        return self._commit(
            session,
            meta,
            decision,
            action=action,
            lease_owner=lease_owner,
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
        replayed = self._check_version(
            session_id, expected_state_version, meta,
            replay=(scope, idempotency_key, request_hash) if idempotency_key else None,
        )
        if replayed is not None:
            return replayed

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
                "review_verdict": getattr(record, "review_verdict", "pass"),
                "input_version": input_version,
                "spec_expected_revision": spec_expected_revision,
                "content_hash": content_hash,
                "confirmed_at": handoff["confirmed_at"],
                "spec_revision_id": None,
                # 导出不在 §6 的 6 个操作内，默认不暴露。
                "export_url": (
                    f"/api/v1/discussions/{session_id}/export" if self.export_enabled else None
                ),
            },
            "session": self._render(session, {**meta, "state_version": meta["state_version"] + 1, "signed_draft_id": draft_id}),
        }
        # 状态推进、签收记录、幂等键三者同事务
        replayed = self._save_with_replay(
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
        return replayed if replayed is not None else (200, body)

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

    def _check_version(
        self, session_id: str, expected: int, meta: Mapping[str, Any],
        *, replay: tuple[str, str, str] | None = None,
    ) -> tuple[int, dict[str, Any]] | None:
        if meta["state_version"] != expected:
            if replay is not None and (recorded := self._replay(*replay)) is not None:
                return recorded
            raise ApiError(
                "REVISION_CONFLICT",
                "会话已被其他写入更新，请刷新后重试",
                status=409,
                detail={"state_version": meta["state_version"], "expected_state_version": expected},
            )
        return None

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

        def decode_payload(model: str) -> Any:
            try:
                return decode(self.models[model], payload, "payload")
            except CodecError as exc:
                raise ApiError("VALIDATION_FAILED", str(exc), status=422) from exc

        handler = {
            "pause": lambda: engine.pause(session, payload.get("reason", "")),
            "resume": lambda: engine.resume(session),
            "cancel": lambda: engine.cancel(session, payload.get("reason", "")),
            "require_changes": lambda: engine.require_changes(session, payload.get("reason", ""), draft_id=payload.get("draft_id")),
            "abandon_task": lambda: engine.abandon_task(
                session, payload.get("task_id", ""), payload.get("reason", "")
            ),
            "submit_output": lambda: engine.submit_output(
                session, decode_payload("SpeakerOutput")
            ),
            "submit_review": lambda: engine.submit_review(
                session, decode_payload("ReviewResult")
            ),
            "submit_agenda": lambda: engine.submit_agenda(
                session, decode_payload("Agenda")
            ),
            "propose": lambda: engine.propose(
                session, decode_payload("HostProposal")
            ),
        }
        if action in {"retry", "run_agents"}:
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
        lease_owner: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if not decision.ok:
            return self._reject(decision)

        body = {"decision": self._decision_view(decision)}
        if lease_owner is None:
            previous_last_seq = meta["last_seq"]
            new_messages = [m for m in getattr(session, "messages", ()) if m.seq > previous_last_seq]
            body.update(
                session=self._render(session, {**meta, "state_version": meta["state_version"] + 1}),
                new_messages=[self._message_view(message) for message in new_messages],
                tasks=[self._task_view(task) for task in getattr(decision, "tasks", ())],
            )
        key_tuple = None
        if idempotency is not None:
            scope, key, request_hash = idempotency
            key_tuple = (scope, key, request_hash, 200, json.dumps(body, ensure_ascii=False))
        schedule = lease_owner is None and (
            action in {"run_agents", "submit_agenda", "submit_output", "submit_review", "propose", "require_changes", "resume"}
            or (action == "post_message" and payload["substantive"])
        )
        replayed = self._save_with_replay(
            session,
            meta["state_version"],
            action=action,
            decision_code=self._code(decision),
            request_id=request_id,
            payload=payload,
            idempotency=key_tuple,
            agent_mode=self.agent_mode if schedule else "off",
            lease_owner=lease_owner,
        )
        return replayed if replayed is not None else (200, body)

    def _save_with_replay(self, session, version: int, *, idempotency, **fields):
        try:
            self.repo.save_after_decision(session, version, idempotency=idempotency, **fields)
        except IdempotencyRecorded:
            if idempotency is not None:
                scope, key, request_hash, _, _ = idempotency
                replayed = self._replay(scope, key, request_hash)
                if replayed is not None:
                    return replayed
            raise
        return None

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
        # Older idempotent responses may still contain the retired field.
        if isinstance(body, dict):
            body.pop("budget", None)
            if isinstance(body.get("session"), dict):
                body["session"].pop("budget", None)
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

    @staticmethod
    def _clarification(session: Any) -> dict[str, Any] | None:
        if session.status.value != "clarifying" or session.waiting_for.value != "user_input":
            return None
        message = next((item for item in reversed(session.messages)
                        if item.kind.value == "clarification" and item.input_version == session.input_version), None)
        if message is None:
            return None
        questions = list(getattr(message, "clarification_questions", ()))
        options = {}
        if message.role.value == "reviewer":
            review = next((item for item in reversed(session.reviews) if item.input_version == session.input_version), None)
            if review is not None:
                options = getattr(review, "question_options", {})
                if not questions:
                    legacy = next((finding.summary for finding in review.findings
                                   if finding.finding_id == f"{review.review_id}:questions"), "")
                    questions = legacy.split("；")
        elif message.role.value == "host":
            if not questions:
                questions = message.text.removeprefix("需要用户补充关键事实：").split("；")
            agenda = getattr(session, "agenda", None)
            options = getattr(agenda, "question_options", {})
        questions = list(dict.fromkeys(q.strip() for q in questions if q.strip()))
        picks = {}
        for question in questions:
            entries = list(dict.fromkeys(option.strip() for option in options.get(question, ()) if option.strip()))
            if len(entries) >= 4:
                picks[question] = entries[:4]
        return {"message_seq": message.seq, "questions": questions, "options": picks}

    @staticmethod
    def _usage(attempts: list[dict[str, Any]]) -> dict[str, Any]:
        by_task: dict[str, dict[str, Any]] = {}
        for attempt in attempts:
            usage = attempt["usage"] or {}
            elapsed = attempt.get("elapsed_seconds")
            code = (attempt["error"] or {}).get("code")
            if elapsed is None and not usage:
                continue  # Old or interrupted records can have an unknown provider outcome.
            summary = by_task.setdefault(attempt["task_kind"], {
                "calls": 0, "tokens": 0, "elapsed_seconds": 0.0, "retries": 0, "invalid_outputs": 0,
            })
            summary["calls"] += 1
            summary["tokens"] += usage.get("total_tokens") or 0
            summary["elapsed_seconds"] += elapsed or 0
            summary["retries"] += int(attempt["attempt_no"] > 1)
            summary["invalid_outputs"] += int(code == "invalid_output")
        total = {key: sum(bucket[key] for bucket in by_task.values()) for key in (
            "calls", "tokens", "elapsed_seconds", "retries", "invalid_outputs",
        )}
        return {**total, "by_task": by_task}

    def _render(self, session: Any, meta: Mapping[str, Any]) -> dict[str, Any]:
        attempts = self.repo.list_attempts(session.session_id)
        signed_drafts = self.repo.signed_draft_ids(session.session_id)
        if signed_draft := meta.get("signed_draft_id"):
            signed_drafts.add(signed_draft)
        draft = getattr(session, "current_draft", None)
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
            "clarification": self._clarification(session),
            "clarification_answers": {str(message.seq): encode(message.clarification_answers)
                                      for message in session.messages if getattr(message, "clarification_answers", ())},
            "superseded_answer_seqs": [message.replaces_answer_seq for message in session.messages
                                       if getattr(message, "replaces_answer_seq", None) is not None],
            "participants": self._participants(session, attempts),
            "usage": self._usage(attempts),
            "pending_tasks": [self._task_view(task) for task in getattr(session, "tasks", {}).values()],
            "agent_mode": self.agent_mode,
            "active_draft": self._draft_view(session, draft) if draft else None,
            "scorecard": self._scorecard_view(session),
            # §6 要求返回"草稿、评审"：历史以版本摘要呈现，正文在 active_draft
            "drafts": [
                {
                    "draft_id": item.draft_id,
                    "version": item.version,
                    "input_version": item.input_version,
                    "current": item.draft_id == getattr(draft, "draft_id", None),
                    "signed": item.draft_id in signed_drafts,
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
                    "verified_findings": [encode(finding) for finding in getattr(item, "verified_findings", ()) or ()],
                }
                for item in getattr(session, "reviews", ()) or ()
            ],
            "open_issues": [encode(issue) for issue in session.open_issues()],
        }

    def _draft_view(self, session: Any, draft: Any) -> dict[str, Any]:
        review = next((item for item in reversed(getattr(session, "reviews", ()))
                       if item.draft_id == draft.draft_id), None)
        scorecard_id = getattr(draft, "scorecard_id", None)
        return {
            "draft_id": draft.draft_id,
            "version": draft.version,
            "input_version": draft.input_version,
            "parent_id": getattr(draft, "parent_id", None),
            "open_issue_ids": list(draft.open_issue_ids),
            "review_verdict": getattr(review.verdict, "value", review.verdict) if review else None,
            "content": dict(draft.content),
            "agreements": list(getattr(draft, "agreements", ()) or ()),
            "alternatives": list(getattr(draft, "alternatives", ()) or ()),
            "disagreements": list(getattr(draft, "disagreements", ()) or ()),
            "assumptions": list(getattr(draft, "assumptions", ()) or ()),
            "risks": list(getattr(draft, "risks", ()) or ()),
            "scorecard_id": scorecard_id,
            "chosen_option_id": getattr(draft, "chosen_option_id", None),
            "scorecard": self._scorecard_view(session, scorecard_id) if scorecard_id else None,
        }

    def _scorecard_view(self, session: Any, scorecard_id: str | None = None) -> dict[str, Any] | None:
        card = next((
            card for card in reversed(getattr(session, "scorecards", ()) or ())
            if (card.scorecard_id == scorecard_id if scorecard_id else card.input_version == session.input_version)
        ), None)
        if card is None:
            return None
        ranked = rank_options(card.options, card.weights)
        return {
            "scorecard_id": card.scorecard_id,
            "topic_id": card.topic_id,
            "input_version": card.input_version,
            "chosen_option_id": card.chosen_option_id,
            "rationale": card.rationale,
            "stances": {role.value: [encode(stance) for stance in positions] for role, positions in card.stances.items()},
            "weights": {dimension.value: weight for dimension, weight in card.weights.items()},
            "options": [
                {
                    "option_id": option.option_id,
                    "title": option.title,
                    "scores": {dimension.value: value for dimension, value in option.scores.items()},
                    "total": option.total,
                    "rank": option.rank,
                }
                for option in ranked
            ],
        }

    def _participants(self, session: Any, attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """参与者状态：调用尝试 > 本轮已发言 > 已派发待执行。"""
        latest: dict[str, dict[str, Any]] = {}
        pending_tasks = getattr(session, "tasks", {})
        current_ids = set(pending_tasks) | {m.task_id for m in session.messages if m.round_no == session.round_no}
        if session.current_draft:
            current_ids.add(f"{session.current_draft.draft_id}:agent:review")
        for attempt in attempts:
            if attempt["input_version"] != session.input_version:
                continue
            if attempt["logical_task_id"] not in current_ids and attempt["task_kind"] != "agenda":
                continue
            role = attempt["role"]
            previous = latest.get(role)
            priority = (attempt["logical_task_id"] in pending_tasks, attempt["started_at"], attempt["attempt_no"])
            if previous is None or priority >= previous["priority"]:
                latest[role] = {"priority": priority, "participant": {
                    "role": role, "status": str(attempt["status"]).lower(), "attempt_no": attempt["attempt_no"],
                    "error_code": (attempt["error"] or {}).get("code"),
                    "diagnostic": (attempt["error"] or {}).get("diagnostic"),
                }}

        done = {
            message.role.value
            for message in session.messages
            if message.round_no == session.round_no
        }
        queued = {
            getattr(task.role, "value", str(task.role))
            for task in pending_tasks.values()
        }
        roles = sorted(set(latest) | done | queued)
        return [
            (latest[role]["participant"] if role in latest else None)
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
