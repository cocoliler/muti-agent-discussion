"""错误映射与信封（实施方案 §7.0、附录 B）。

两条通道刻意分开：

* **接口层错误**（入参非法、版本冲突、幂等冲突）走 HTTP 4xx + 错误信封。
* **编排拒绝**（`decision.ok is False`，如等待评审、预算耗尽、越界点名）走
  HTTP 200 + decision 信封 —— 它们是可诊断状态，不是请求失败。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: 接口错误码 -> HTTP 状态码（沿用 FlowStudio 既有 ErrorCode 集合）
ERROR_STATUS: dict[str, int] = {
    "VALIDATION_FAILED": 422,
    "REVISION_CONFLICT": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "NOT_FOUND": 404,
    "PAYLOAD_TOO_LARGE": 413,
    "QUEUE_FULL": 429,
    "STORAGE_BUSY": 503,
    "INTERNAL_ERROR": 500,
}

#: 引擎 ReasonCode -> (HTTP 状态码, 接口错误码)。未列出的走 200 + decision。
DECISION_ERRORS: dict[str, tuple[int, str]] = {
    "cross_session_reference": (422, "VALIDATION_FAILED"),
    "cross_session_result": (422, "VALIDATION_FAILED"),
    "contract_mismatch": (422, "VALIDATION_FAILED"),
    "invalid_agenda": (422, "VALIDATION_FAILED"),
    "invalid_topic": (422, "VALIDATION_FAILED"),
    "invalid_issue": (422, "VALIDATION_FAILED"),
    "invalid_assignment": (422, "VALIDATION_FAILED"),
    "invalid_target_role": (422, "VALIDATION_FAILED"),
    "illegal_reply_target": (422, "VALIDATION_FAILED"),
    "unknown_task": (404, "NOT_FOUND"),
    "unknown_issue": (404, "NOT_FOUND"),
    "stale_input_version": (409, "REVISION_CONFLICT"),
    "stale_round": (409, "REVISION_CONFLICT"),
    "stale_draft": (409, "REVISION_CONFLICT"),
    "review_stale": (409, "REVISION_CONFLICT"),
    "invalid_status": (409, "REVISION_CONFLICT"),
    "invalid_transition": (409, "REVISION_CONFLICT"),
}


class ApiError(Exception):
    """接口层错误，由处理器转成错误信封。"""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        status: int | None = None,
        issues: Iterable[Mapping[str, str]] | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = status or ERROR_STATUS.get(code, 400)
        self.issues = [dict(issue) for issue in (issues or [])]
        self.detail = dict(detail or {})


def decision_status(code: str) -> int:
    """编排决策结果码对应的 HTTP 状态码；默认 200。"""
    return DECISION_ERRORS.get(code, (200, ""))[0]


def decision_error_code(code: str) -> str | None:
    return DECISION_ERRORS.get(code, (0, ""))[1] or None


def error_body(
    code: str,
    message: str,
    *,
    issues: Iterable[Mapping[str, str]] | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "issues": [dict(issue) for issue in (issues or [])],
            "request_id": request_id,
        }
    }
