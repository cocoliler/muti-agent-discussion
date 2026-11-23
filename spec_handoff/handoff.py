"""规格版本 API 交接适配器。

对应设计文档 6.2：签收后把同一份正文提交到 FlowStudio
`POST /api/v1/projects/{project_id}/spec-revisions`，请求体为
`{expected_revision, content}`，写命令携带 `Idempotency-Key`。

关键约定（与 FlowStudio 后端一致）：

- 首次保存 `expected_revision = 0`，服务端 CAS，版本变化返回 409 `REVISION_CONFLICT`；
- 同一 `Idempotency-Key` 与同一请求体返回原结果；同键异内容返回 409
  `IDEMPOTENCY_CONFLICT`，因此**改版本号重发必须换新键**；
- `STORAGE_BUSY`、网络错误、超时可用同一键原样重发。

本模块不落库：`SubmissionRecord.to_dict()` 供后端持久化「待提交正文 + 目标版本 + 幂等键」，
成功后标记完成，避免响应丢失时生成重复版本。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

from .codes import RETRYABLE_API_CODES, ApiCode, Code, Finding
from .export import MAX_EXPORT_BYTES, content_hash, dump_bytes, order_content
from .signoff import SpecHandoff

API_PREFIX = "/api/v1"
_BACKOFF_SECONDS = (1, 3)


class SubmissionStatus(str, Enum):
    """提交状态。`CONFLICT` 表示版本已变化，需要读取最新版本后另起一次提交。"""

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = b""

    def json_body(self) -> Mapping[str, Any] | None:
        if not self.body:
            return None
        try:
            payload = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, Mapping) else None

    @property
    def error_code(self) -> str | None:
        payload = self.json_body()
        if not isinstance(payload, Mapping):
            return None
        error = payload.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("code"), str):
            return str(error["code"])
        return None

    @property
    def error_message(self) -> str:
        payload = self.json_body()
        if isinstance(payload, Mapping) and isinstance(payload.get("error"), Mapping):
            message = payload["error"].get("message")
            if isinstance(message, str):
                return message
        return self.body[:500].decode("utf-8", "replace")


class Transport(Protocol):
    """发送一个 HTTP 请求。测试注入假实现即可，不需要真实服务。"""

    def __call__(self, request: HttpRequest) -> HttpResponse: ...


def urllib_transport(request: HttpRequest, *, timeout: float = 10.0) -> HttpResponse:
    """基于标准库的默认传输；HTTP 错误码作为响应返回，网络故障向上抛异常。"""

    prepared = urllib.request.Request(
        request.url,
        data=request.body or None,
        headers=dict(request.headers),
        method=request.method,
    )
    try:
        with urllib.request.urlopen(prepared, timeout=timeout) as response:
            return HttpResponse(status=int(response.status), body=response.read())
    except urllib.error.HTTPError as error:
        return HttpResponse(status=int(error.code), body=error.read())


@dataclass(frozen=True)
class Attempt:
    """一次提交尝试。与 FlowStudio 的 `RoleAttempt` 同思路：尝试与结果分离记录。"""

    attempt_no: int
    status: int | None
    code: str
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_no": self.attempt_no,
            "status": self.status,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class SubmissionRecord:
    """一次交接提交。可序列化后交给持久化层，响应丢失时按同键重发。"""

    handoff: SpecHandoff
    expected_revision: int = 0
    idempotency_key: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: SubmissionStatus = SubmissionStatus.PENDING
    spec_revision_id: str | None = None
    spec_revision: int | None = None
    spec_hash: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    error: str | None = None
    findings: tuple[Finding, ...] = ()

    # ------------------------------------------------------------------ 视图
    @property
    def project_id(self) -> str:
        return self.handoff.project_id

    @property
    def payload(self) -> dict[str, Any]:
        """与契约 `SpecRevisionWriteRequest` 同形。"""

        return {
            "expected_revision": self.expected_revision,
            "content": order_content(self.handoff.content),
        }

    @property
    def body(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def check_payload(self) -> tuple[Finding, ...]:
        """本地预检：正文必须与签收时一致，且不超过接口上限。"""

        findings: list[Finding] = []
        current = content_hash(self.handoff.content)
        if current != self.handoff.content_hash:
            findings.append(
                Finding(Code.INVALID_VALUE, "/content", "正文在签收后发生变化，不能提交")
            )
        if len(dump_bytes(self.handoff.content)) > MAX_EXPORT_BYTES:
            findings.append(Finding(Code.EXPORT_TOO_LARGE, "/content", "正文超过 1 MiB 接口上限"))
        return tuple(findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "handoff": {**self.handoff.as_dict(), "content": dict(self.handoff.content)},
            "expected_revision": self.expected_revision,
            "idempotency_key": self.idempotency_key,
            "status": self.status.value,
            "spec_revision_id": self.spec_revision_id,
            "spec_revision": self.spec_revision,
            "spec_hash": self.spec_hash,
            "attempts": [item.as_dict() for item in self.attempts],
            "error": self.error,
            "findings": [item.as_dict() for item in self.findings],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SubmissionRecord:
        handoff_payload = payload["handoff"]
        handoff = SpecHandoff(
            session_id=str(handoff_payload["session_id"]),
            project_id=str(handoff_payload["project_id"]),
            draft_id=str(handoff_payload["draft_id"]),
            draft_version=int(handoff_payload["draft_version"]),
            review_id=str(handoff_payload["review_id"]),
            input_version=int(handoff_payload["input_version"]),
            content=handoff_payload["content"],
            content_hash=str(handoff_payload["content_hash"]),
            confirmed_at=str(handoff_payload["confirmed_at"]),
        )
        return cls(
            handoff=handoff,
            expected_revision=int(payload.get("expected_revision", 0)),
            idempotency_key=str(payload["idempotency_key"]),
            status=SubmissionStatus(str(payload.get("status", "PENDING"))),
            spec_revision_id=payload.get("spec_revision_id"),
            spec_revision=payload.get("spec_revision"),
            spec_hash=payload.get("spec_hash"),
            attempts=[
                Attempt(
                    attempt_no=int(item.get("attempt_no", 0)),
                    status=item.get("status"),
                    code=str(item.get("code", "")),
                    message=str(item.get("message", "")),
                )
                for item in payload.get("attempts", ())
                if isinstance(item, Mapping)
            ],
            error=payload.get("error"),
        )


@dataclass(frozen=True)
class LatestRevision:
    """项目当前最新规格版本；`revision == 0` 表示还没有保存过。"""

    revision: int = 0
    revision_id: str | None = None
    content_hash: str | None = None


class HandoffError(Exception):
    """交接前置失败（读取最新版本失败等）。`code` 取自 `ApiCode`。"""

    def __init__(self, code: ApiCode, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class SpecRevisionHandoff:
    """规格版本写入适配器。传输、退避与时钟均可注入，便于测试与离线演示。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        transport: Transport | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport or urllib_transport
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep

    # ---------------------------------------------------------------- 请求构造
    def create(self, handoff: SpecHandoff, *, expected_revision: int = 0) -> SubmissionRecord:
        return SubmissionRecord(handoff=handoff, expected_revision=expected_revision)

    def _url(self, project_id: str) -> str:
        return f"{self.base_url}{API_PREFIX}/projects/{quote(project_id, safe='')}/spec-revisions"

    def _request(self, record: SubmissionRecord) -> HttpRequest:
        return HttpRequest(
            method="POST",
            url=self._url(record.project_id),
            body=record.body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "Idempotency-Key": record.idempotency_key,
            },
        )

    # ------------------------------------------------------------------ 提交
    def submit(self, record: SubmissionRecord) -> SubmissionRecord:
        """提交一次。返回同一个记录对象（就地更新状态），不抛出业务错误。"""

        if record.status is SubmissionStatus.SUCCEEDED:
            return record

        findings = record.check_payload()
        if findings:
            record.findings = findings
            record.status = SubmissionStatus.FAILED
            record.error = "正文未通过本地预检"
            return record

        request = self._request(record)
        for attempt_no in range(1, self._max_attempts + 1):
            try:
                response = self._transport(request)
            except Exception as error:  # 网络故障：同键重发
                record.attempts.append(
                    Attempt(attempt_no, None, ApiCode.NETWORK_ERROR.value, str(error))
                )
                record.status = SubmissionStatus.PENDING
                record.error = f"网络错误：{error}"
                if attempt_no < self._max_attempts:
                    self._sleep(_BACKOFF_SECONDS[min(attempt_no - 1, len(_BACKOFF_SECONDS) - 1)])
                    continue
                return record

            code = response.error_code
            success = response.status in (200, 201)
            record.attempts.append(
                Attempt(
                    attempt_no,
                    response.status,
                    code or ("OK" if success else ApiCode.UNEXPECTED.value),
                    response.error_message if not success else "",
                )
            )
            if success:
                payload = response.json_body() or {}
                record.status = SubmissionStatus.SUCCEEDED
                record.spec_revision_id = _as_str(payload.get("id"))
                record.spec_revision = _as_int(payload.get("revision"))
                record.spec_hash = _as_str(payload.get("hash"))
                record.error = None
                return record

            if code == ApiCode.REVISION_CONFLICT.value:
                record.status = SubmissionStatus.CONFLICT
                record.error = response.error_message
                return record
            if code == ApiCode.IDEMPOTENCY_CONFLICT.value:
                record.status = SubmissionStatus.FAILED
                record.error = response.error_message
                return record

            retryable = code in {item.value for item in RETRYABLE_API_CODES} or (
                500 <= response.status < 600
            )
            if retryable and attempt_no < self._max_attempts:
                record.status = SubmissionStatus.PENDING
                record.error = response.error_message
                self._sleep(_BACKOFF_SECONDS[min(attempt_no - 1, len(_BACKOFF_SECONDS) - 1)])
                continue

            record.status = SubmissionStatus.FAILED
            record.error = response.error_message or f"HTTP {response.status}"
            return record

        return record

    # ------------------------------------------------------------ 冲突与读取
    def rebase(self, record: SubmissionRecord, expected_revision: int) -> SubmissionRecord:
        """版本冲突后另起一次提交：更新期望版本并换新幂等键（请求体已变化）。"""

        return SubmissionRecord(
            handoff=record.handoff,
            expected_revision=expected_revision,
            idempotency_key=str(uuid.uuid4()),
        )

    def latest_revision(self, project_id: str) -> LatestRevision:
        """读取项目最新规格版本，用于冲突后确定新的 `expected_revision`。"""

        url = f"{self._url(project_id)}?limit=1"
        try:
            response = self._transport(HttpRequest(method="GET", url=url))
        except Exception as error:
            raise HandoffError(ApiCode.NETWORK_ERROR, f"读取最新版本失败：{error}") from error

        if response.status == 404:
            raise HandoffError(ApiCode.NOT_FOUND, response.error_message, status=404)
        if response.status != 200:
            code = _api_code(response.error_code)
            raise HandoffError(code, response.error_message, status=response.status)

        payload = response.json_body() or {}
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return LatestRevision()
        head = items[0] if isinstance(items[0], Mapping) else {}
        return LatestRevision(
            revision=_as_int(head.get("revision")) or 0,
            revision_id=_as_str(head.get("id")),
            content_hash=_as_str(head.get("hash")),
        )


def _api_code(value: str | None) -> ApiCode:
    if value is None:
        return ApiCode.UNEXPECTED
    try:
        return ApiCode(value)
    except ValueError:
        return ApiCode.UNEXPECTED


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = [
    "API_PREFIX",
    "Attempt",
    "HandoffError",
    "HttpRequest",
    "HttpResponse",
    "LatestRevision",
    "SpecRevisionHandoff",
    "SubmissionRecord",
    "SubmissionStatus",
    "Transport",
    "urllib_transport",
]
