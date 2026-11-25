"""REST 接口层：``/api/v1/discussions``（实施方案 §7）。

接线方式（不改动既有 demo 路由）::

    from backend.discussions.routes import install
    install(app)          # 追加路由与异常处理器，仅此一行

接口字段定稿时，本文件与 ``schemas.py``/``errors.py`` 是唯一需要改动的三处。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from spec_handoff import check_export, content_hash, dump_text

from .application import DiscussionService
from .agent_execution import AgentExecution
from .errors import ApiError, error_body
from .repository import DiscussionRepository, StorageBusy, VersionConflict
from .schemas import (
    ActionRequest,
    ConfirmEnvelope,
    ConfirmRequest,
    CreateDiscussionRequest,
    DecisionEnvelope,
    MessagePage,
    PostMessageRequest,
    SessionView,
)

#: 约束：单个请求最大 1 MiB
MAX_PAYLOAD_BYTES = 1 << 20

DEFAULT_DB = Path(os.getenv("DISCUSSION_DB", str(Path(__file__).resolve().parent.parent / ".data" / "discussions.sqlite3")))

_service: DiscussionService | None = None


def build_service(db_path: str | Path | None = None) -> DiscussionService:
    repository = DiscussionRepository(db_path or DEFAULT_DB)
    repository.init_schema()
    repository.mark_interrupted_attempts()
    return DiscussionService(repository)


def get_service() -> DiscussionService:
    global _service
    if _service is None:
        _service = build_service()
    return _service


def _canonical_hash(payload: BaseModel, **legacy_fields: Any) -> str:
    fields = {**payload.model_dump(mode="json"), **legacy_fields}
    raw = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _uuid(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ApiError(
            "VALIDATION_FAILED",
            f"{field} 不是合法 UUID",
            status=422,
            issues=[{"path": f"/{field}", "message": "需要 UUID 字符串"}],
        ) from exc


def _request_id(request: Request) -> str:
    """接口约定要求信封里的 request_id 非空；缺省时生成一个，便于日志串联。"""
    supplied = (request.headers.get("X-Request-Id") or "").strip()
    if supplied:
        return supplied[:200]
    return f"req-{uuid.uuid4()}"


def _require_key(key: str | None) -> str:
    if not key:
        raise ApiError(
            "VALIDATION_FAILED",
            "写命令必须携带 Idempotency-Key",
            status=422,
            issues=[{"path": "/Idempotency-Key", "message": "缺少幂等键"}],
        )
    try:
        uuid.UUID(key)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ApiError(
            "VALIDATION_FAILED",
            "Idempotency-Key 必须是 UUID 字符串",
            status=422,
            issues=[{"path": "/Idempotency-Key", "message": "格式非法"}],
        ) from exc
    return key


def spec_download(content: Any, *, filename: str = "design-spec.json") -> Response:
    """把签收正文写成 FlowStudio「导入规格 JSON」可直接读取的文件。

    导出统一走 `spec_handoff.export`：契约字段顺序、2 空格缩进、UTF-8 无 BOM、LF 行尾，
    并在写出前重跑一次结构 + 1 MiB 校验，避免导出 FlowStudio 会拒绝的正文。
    响应头 `X-Content-Hash` 与 FlowStudio `content_hash()` 同一算法，可直接比对。
    """

    findings = check_export(content)
    if findings:
        raise ApiError(
            "VALIDATION_FAILED",
            "签收正文未通过交付校验，无法导出",
            status=409,
            issues=[item.as_dict() for item in findings],
        )
    body = dump_text(content)
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Hash": content_hash(content),
        },
    )


def create_router(
    provider: Callable[[], DiscussionService] = get_service,
    *,
    with_export: bool = False,
    with_agents: bool = False,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["discussions"])
    agents = AgentExecution() if with_agents else None

    def configured_service() -> DiscussionService:
        service = provider()
        if agents is not None:
            service.agent_mode = agents.mode
        return service

    service_dep = Depends(configured_service)

    def export_url(sid: str) -> str | None:
        return f"/api/v1/discussions/{sid}/export" if with_export else None

    @router.post(
        "/projects/{project_id}/discussions",
        response_model=SessionView,
        status_code=201,
        operation_id="createDiscussion",
    )
    def create_discussion(
        project_id: str,
        body: CreateDiscussionRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        service: DiscussionService = service_dep,
    ) -> Any:
        pid = _uuid(project_id, "project_id")
        key = _require_key(idempotency_key)
        status, payload = service.create(
            pid,
            body.idea,
            idempotency_key=key,
            # Preserve idempotency keys recorded by the previous create schema.
            request_hash=_canonical_hash(body, limits=None),
            scope=f"POST /api/v1/projects/{pid}/discussions",
            request_id=_request_id(request),
        )
        if agents is not None:
            background_tasks.add_task(agents.run, service, payload["id"])
        return JSONResponse(payload, status_code=status)

    @router.get("/discussions/{session_id}", response_model=SessionView, operation_id="getDiscussion")
    def get_discussion(session_id: str, service: DiscussionService = service_dep) -> Any:
        sid = _uuid(session_id, "session_id")
        state = service.view(sid)
        if state["status"] == "completed":
            state["export_url"] = export_url(sid)
        return state

    @router.get(
        "/discussions/{session_id}/messages",
        response_model=MessagePage,
        operation_id="listDiscussionMessages",
    )
    def list_messages(
        session_id: str,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
        service: DiscussionService = service_dep,
    ) -> Any:
        return service.messages(_uuid(session_id, "session_id"), after_seq, limit)

    @router.post(
        "/discussions/{session_id}/messages",
        response_model=DecisionEnvelope,
        operation_id="postDiscussionMessage",
    )
    def post_message(
        session_id: str,
        body: PostMessageRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        service: DiscussionService = service_dep,
    ) -> Any:
        sid = _uuid(session_id, "session_id")
        key = _require_key(idempotency_key)
        status, payload = service.post_message(
            sid,
            text=body.text,
            reply_to_seq=body.reply_to_seq,
            **({"clarification_answers": tuple(answer.model_dump() for answer in body.clarification_answers)}
               if body.clarification_answers else {}),
            **({"replaces_answer_seq": body.replaces_answer_seq} if body.replaces_answer_seq is not None else {}),
            substantive=body.substantive,
            expected_state_version=body.expected_state_version,
            input_version=body.input_version,
            idempotency_key=key,
            request_hash=_canonical_hash(body),
            scope=f"POST /api/v1/discussions/{sid}/messages",
            request_id=_request_id(request),
        )
        if agents is not None and body.substantive and payload["decision"]["ok"]:
            background_tasks.add_task(agents.run, service, sid)
        return JSONResponse(payload, status_code=status)

    @router.post(
        "/discussions/{session_id}/actions",
        response_model=DecisionEnvelope,
        operation_id="postDiscussionAction",
    )
    def post_action(
        session_id: str,
        body: ActionRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        service: DiscussionService = service_dep,
    ) -> Any:
        sid = _uuid(session_id, "session_id")
        key = _require_key(idempotency_key)
        status, payload = service.act(
            sid,
            action=body.type,
            payload=body.payload,
            expected_state_version=body.expected_state_version,
            idempotency_key=key,
            request_hash=_canonical_hash(body),
            scope=f"POST /api/v1/discussions/{sid}/actions",
            request_id=_request_id(request),
        )
        if agents is not None and payload["decision"]["ok"] and body.type in {"run_agents", "submit_agenda", "submit_output", "propose",
                                                      "require_changes", "resume"}:
            background_tasks.add_task(agents.run, service, sid)
        return JSONResponse(payload, status_code=status)

    @router.post(
        "/discussions/{session_id}/confirm",
        response_model=ConfirmEnvelope,
        operation_id="confirmDiscussion",
    )
    def confirm_discussion(
        session_id: str,
        body: ConfirmRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        service: DiscussionService = service_dep,
    ) -> Any:
        sid = _uuid(session_id, "session_id")
        key = _require_key(idempotency_key)
        status, payload = service.confirm(
            sid,
            draft_id=body.draft_id,
            input_version=body.input_version,
            expected_state_version=body.expected_state_version,
            spec_expected_revision=body.spec_expected_revision,
            idempotency_key=key,
            request_hash=_canonical_hash(body),
            scope=f"POST /api/v1/discussions/{sid}/confirm",
            request_id=_request_id(request),
        )
        # 下载地址取决于当前应用挂载的路由，旧签收回执也可在升级后重放。
        if payload.get("handoff"):
            payload["handoff"]["export_url"] = export_url(sid)
        return JSONResponse(payload, status_code=status)

    if with_export:
        # 导出不在 §6 的 6 个讨论操作内，默认不注册。
        @router.get("/discussions/{session_id}/export", response_model=None)
        def export_discussion(
            session_id: str,
            draft_id: str | None = Query(default=None, max_length=200),
            service: DiscussionService = service_dep,
        ) -> Any:
            sid = _uuid(session_id, "session_id")
            payload = (
                service.export_content(sid)
                if draft_id is None
                else service.export_content(sid, draft_id=draft_id)
            )
            return spec_download(payload["content"])

    return router


async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    body = error_body(exc.code, exc.message, issues=exc.issues, request_id=_request_id(request))
    if exc.detail:
        body["error"]["detail"] = exc.detail
    return JSONResponse(body, status_code=exc.status)


async def _version_conflict_handler(request: Request, exc: VersionConflict) -> JSONResponse:
    body = error_body(
        "REVISION_CONFLICT",
        "会话已被其他写入更新，请刷新后重试",
        request_id=_request_id(request),
    )
    body["error"]["detail"] = {"state_version": exc.current, "expected_state_version": exc.expected}
    return JSONResponse(body, status_code=409)


async def _storage_busy_handler(request: Request, exc: StorageBusy) -> JSONResponse:
    body = error_body("STORAGE_BUSY", "存储繁忙，请稍后重试", request_id=_request_id(request))
    return JSONResponse(body, status_code=503)


class PayloadLimitMiddleware:
    """ASGI 层请求体上限：接口约束为单请求最大 1 MiB，超出返回 413。

    必须放在 ASGI 层而不是 FastAPI 依赖里：依赖执行时请求体已被读完，
    无法阻止大包进入内存，也拿不到稳定的 413 语义。
    """

    def __init__(self, app: Any, max_bytes: int = MAX_PAYLOAD_BYTES, prefix: str = "/api/v1/") -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.prefix = prefix

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await self._too_large(scope, receive, send, headers)
            return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                await self._too_large(scope, receive, send, headers)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        buffered = b"".join(chunks)
        replayed = False

        async def replay() -> Any:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope: Any, receive: Any, send: Any, headers: dict[str, str]) -> None:
        body = error_body(
            "PAYLOAD_TOO_LARGE",
            f"请求体超过 {self.max_bytes} 字节上限",
            request_id=headers.get("x-request-id") or f"req-{uuid.uuid4()}",
        )
        await JSONResponse(body, status_code=413)(scope, receive, send)


def install(
    app: Any,
    service: DiscussionService | None = None,
    *,
    with_export: bool = False,
    with_agents: bool = False,
) -> None:
    """把讨论接口挂到既有 FastAPI 应用上（不改动 demo 路由）。

    ``with_export`` 默认关闭；需要 JSON 导出时显式置 True。
    """
    provider: Callable[[], DiscussionService] = (lambda: service) if service is not None else get_service
    if service is not None:
        service.export_enabled = with_export
    app.include_router(create_router(provider, with_export=with_export, with_agents=with_agents))
    app.add_middleware(PayloadLimitMiddleware)
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(VersionConflict, _version_conflict_handler)
    app.add_exception_handler(StorageBusy, _storage_busy_handler)


def create_app(service: DiscussionService | None = None, *, with_agents: bool = False) -> Any:
    """独立启动用（本地调试）：``uvicorn backend.discussions.routes:create_app --factory``。"""
    from fastapi import FastAPI

    app = FastAPI(title="多 Agent 需求讨论工作台", version="0.1.0")
    install(app, service, with_agents=with_agents)
    return app
