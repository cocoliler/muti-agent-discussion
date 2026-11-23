"""会话状态机。

阶段迁移是服务端决定，主持人只提交调度建议。非法迁移在这里被拒绝，
不会出现“评审通过但仍有阻断项”之类的半合法状态。
"""

from __future__ import annotations

from .enums import ReasonCode, SessionStatus

#: 允许的迁移。PAUSED 的恢复目标由 paused_from 决定，见 can_resume。
ALLOWED_TRANSITIONS: dict[tuple[SessionStatus, SessionStatus], str] = {
    (SessionStatus.CLARIFYING, SessionStatus.DISCUSSING): "host_agenda_ready",
    (SessionStatus.DISCUSSING, SessionStatus.CLARIFYING): "host_or_review_needs_user",
    (SessionStatus.DISCUSSING, SessionStatus.REVIEWING): "candidate_draft_frozen",
    (SessionStatus.REVIEWING, SessionStatus.DISCUSSING): "review_revise",
    (SessionStatus.REVIEWING, SessionStatus.CLARIFYING): "review_needs_input",
    (SessionStatus.REVIEWING, SessionStatus.AWAITING_CONFIRMATION): "review_pass",
    (SessionStatus.AWAITING_CONFIRMATION, SessionStatus.COMPLETED): "user_confirm",
    (SessionStatus.AWAITING_CONFIRMATION, SessionStatus.DISCUSSING): "user_requires_changes",
    (SessionStatus.AWAITING_CONFIRMATION, SessionStatus.CLARIFYING): "user_new_input",
}

TERMINAL_STATUSES: frozenset[SessionStatus] = frozenset(
    {SessionStatus.COMPLETED, SessionStatus.CANCELED}
)

PAUSABLE_STATUSES: frozenset[SessionStatus] = frozenset(
    {
        SessionStatus.CLARIFYING,
        SessionStatus.DISCUSSING,
        SessionStatus.REVIEWING,
        SessionStatus.AWAITING_CONFIRMATION,
    }
)

RESUMABLE_STATUSES: frozenset[SessionStatus] = frozenset(
    {
        SessionStatus.CLARIFYING,
        SessionStatus.DISCUSSING,
        SessionStatus.REVIEWING,
        SessionStatus.AWAITING_CONFIRMATION,
    }
)


class IllegalTransition(Exception):
    """阶段迁移被拒绝。reason 进入响应，便于前端给出下一步。"""

    def __init__(
        self,
        current: SessionStatus,
        target: SessionStatus,
        code: ReasonCode = ReasonCode.INVALID_TRANSITION,
    ) -> None:
        super().__init__(
            f"不允许从 {current.value} 迁移到 {target.value}（{code.value}）"
        )
        self.current = current
        self.target = target
        self.code = code


def is_terminal(status: SessionStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_transition(current: SessionStatus, target: SessionStatus) -> bool:
    """检查迁移是否合法。暂停与取消单独由引擎处理。"""

    return (current, target) in ALLOWED_TRANSITIONS


def can_pause(status: SessionStatus) -> bool:
    return status in PAUSABLE_STATUSES


def can_resume(paused_from: SessionStatus | None) -> bool:
    return paused_from in RESUMABLE_STATUSES


def assert_transition(current: SessionStatus, target: SessionStatus) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)
