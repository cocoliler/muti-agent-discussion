"""交付校验的结果码与缺陷结构。

本层只回答两个问题——「这份草稿能不能签收」「签收后能不能被 FlowStudio 导入」。
码表分三类，互不混用：

- `Code`：交付校验的缺陷码，出现在本层的 `Finding` 上；
- `ApiCode`：FlowStudio 规格版本写入接口的错误码（`ErrorCode` 子集）+ 本地传输错误；
- `Level`：缺陷级别，只有 error 会阻断签收与导出。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class Level(str, Enum):
    """缺陷级别。error 阻断签收与导出，warning 只提示。"""

    ERROR = "error"
    WARNING = "warning"


class Code(str, Enum):
    """交付校验缺陷码。固定的字符串取值，便于前后端按码做提示与回归用例。"""

    OK = "OK"
    # ---------------------------------------------------------------- 结构
    SPEC_REQUIRED = "SPEC_REQUIRED"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    DUPLICATE_ID = "DUPLICATE_ID"
    INVALID_VALUE = "INVALID_VALUE"
    # ------------------------------------------------------------ 来源追溯
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_UNKNOWN = "SOURCE_UNKNOWN"
    UNKNOWN_TRACE_ITEM = "UNKNOWN_TRACE_ITEM"
    REQUIRED_UNCOVERED = "REQUIRED_UNCOVERED"
    # ---------------------------------------------------------------- 验收
    ACCEPTANCE_MISSING = "ACCEPTANCE_MISSING"
    ACCEPTANCE_DUPLICATE = "ACCEPTANCE_DUPLICATE"
    # ------------------------------------------------------------ 版本稳定
    STALE_DRAFT = "STALE_DRAFT"
    STALE_INPUT_VERSION = "STALE_INPUT_VERSION"
    ID_DROPPED = "ID_DROPPED"
    # ------------------------------------------------------------ 签收门禁
    INVALID_STATUS = "INVALID_STATUS"
    REVIEW_NOT_PASSED = "REVIEW_NOT_PASSED"
    BLOCKING_FINDING = "BLOCKING_FINDING"
    OPEN_BLOCKING_ISSUE = "OPEN_BLOCKING_ISSUE"
    NOT_SIGNED_OFF = "NOT_SIGNED_OFF"
    # ------------------------------------------------------------ 导出交接
    EXPORT_TOO_LARGE = "EXPORT_TOO_LARGE"


class ApiCode(str, Enum):
    """规格版本写入接口的错误码。

    取值与 FlowStudio `ErrorCode` 一致，便于交接层直接透传；
    只有 `NETWORK_ERROR` / `TIMEOUT` 是本地传输层补充的，属于可重试错误。
    """

    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    QUEUE_FULL = "QUEUE_FULL"
    STORAGE_BUSY = "STORAGE_BUSY"
    RESULT_NOT_READY = "RESULT_NOT_READY"
    ORIGIN_FORBIDDEN = "ORIGIN_FORBIDDEN"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    UNEXPECTED = "UNEXPECTED"


#: 可原样重发同一请求的错误：重发沿用同一幂等键即可。
RETRYABLE_API_CODES: frozenset[ApiCode] = frozenset(
    {ApiCode.STORAGE_BUSY, ApiCode.NETWORK_ERROR, ApiCode.TIMEOUT}
)


@dataclass(frozen=True)
class Finding:
    """一条交付缺陷。`path` 是 DesignSpecContent 的 JSON Pointer，便于定位与回写表单。"""

    code: Code
    path: str
    message: str
    level: Level = Level.ERROR
    requirement_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code.value,
            "level": self.level.value,
            "path": self.path,
            "message": self.message,
        }
        if self.requirement_id is not None:
            payload["requirement_id"] = self.requirement_id
        return payload


def blocking(findings: tuple[Finding, ...] | list[Finding]) -> tuple[Finding, ...]:
    return tuple(finding for finding in findings if finding.level is Level.ERROR)


def describe(findings: tuple[Finding, ...] | list[Finding]) -> str:
    return "；".join(f"{item.code.value}@{item.path}：{item.message}" for item in findings)


def error_body(
    code: ApiCode,
    message: str,
    *,
    findings: tuple[Finding, ...] = (),
    request_id: str = "",
) -> Mapping[str, Any]:
    """与 FlowStudio 错误信封同形：`{error:{code,message,issues,request_id}}`。"""

    return {
        "error": {
            "code": code.value,
            "message": message,
            "issues": [item.as_dict() for item in findings],
            "request_id": request_id,
        }
    }
