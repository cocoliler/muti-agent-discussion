"""编排合法性校验。

这里只判断“这一步能不能走”，不判断角色正文写得好不好（角色输出契约负责），
也不判断规格是否可交付（交付校验负责）。
"""

from __future__ import annotations

from typing import Mapping

from .enums import (
    NAMABLE_ROLES,
    IssueStatus,
    ReasonCode,
    Role,
    Severity,
)
from .models import Agenda, DiscussionSession, Issue

# --------------------------------------------------------------------- 引用


def known_seqs(session: DiscussionSession) -> set[int]:
    return {message.seq for message in session.messages}


def citation_problem(
    session: DiscussionSession,
    source_seqs: tuple[int, ...],
    reply_to_seq: int | None,
) -> tuple[ReasonCode, str] | None:
    """来源与回复必须指向本会话已提交消息，跨会话引用直接拒绝。"""

    known = known_seqs(session)
    for seq in source_seqs:
        if seq not in known:
            return (
                ReasonCode.CROSS_SESSION_REFERENCE,
                f"来源消息 {seq} 不属于当前会话或尚未提交",
            )
    if reply_to_seq is not None:
        if reply_to_seq not in known:
            return (
                ReasonCode.CROSS_SESSION_REFERENCE,
                f"回复目标 {reply_to_seq} 不属于当前会话或尚未提交",
            )
        if reply_to_seq > session.last_seq:
            return (
                ReasonCode.ILLEGAL_REPLY_TARGET,
                f"回复目标 {reply_to_seq} 晚于当前序号 {session.last_seq}",
            )
    return None


# --------------------------------------------------------------------- 议题


def validate_agenda(agenda: Agenda) -> tuple[str, ...]:
    problems: list[str] = []
    if not agenda.summary.strip():
        problems.append("议题摘要不能为空")
    if not agenda.items:
        problems.append("至少需要一个议题")
    seen: set[str] = set()
    for item in agenda.items:
        prefix = item.topic_id or "<空议题 ID>"
        if not item.topic_id.strip():
            problems.append("议题 ID 不能为空")
        elif item.topic_id in seen:
            problems.append(f"{prefix}：议题 ID 重复")
        seen.add(item.topic_id)
        if not item.title.strip():
            problems.append(f"{prefix}：缺少议题标题")
        if not item.question.strip():
            problems.append(f"{prefix}：缺少待讨论问题")
        if not item.required_roles:
            problems.append(f"{prefix}：必须指定必需发言者")
        if not set(item.required_roles) <= set(item.role_scope):
            problems.append(f"{prefix}：必需发言者必须落在点名范围内")
        if not set(item.role_scope) <= NAMABLE_ROLES:
            problems.append(f"{prefix}：点名范围只能是专业角色")
        for assumption in item.assumptions:
            if not all(
                part.strip()
                for part in (
                    assumption.text,
                    assumption.source,
                    assumption.impact,
                    assumption.verification,
                )
            ):
                problems.append(f"{prefix}：假设需同时给出来源、影响和验证方式")
    return tuple(problems)


# ------------------------------------------------------------------- 点名


def validate_assignments(
    session: DiscussionSession,
    assignments: Mapping[Role, tuple[str, ...]],
) -> tuple[ReasonCode, str] | None:
    """点名只能落在议题范围内，且问题必须仍处于开放状态。"""

    item = session.current_item()
    if item is None:
        return (ReasonCode.INVALID_TOPIC, "当前没有活动议题")
    if not assignments:
        return (ReasonCode.INVALID_ASSIGNMENT, "点名回应至少需要一个目标角色")
    for role, issue_ids in assignments.items():
        if role not in NAMABLE_ROLES:
            return (
                ReasonCode.INVALID_TARGET_ROLE,
                f"{role.value} 不在可点名角色范围",
            )
        if role not in item.role_scope:
            return (
                ReasonCode.INVALID_TARGET_ROLE,
                f"{role.value} 不在议题 {item.topic_id} 的点名范围",
            )
        if not issue_ids:
            return (
                ReasonCode.INVALID_ASSIGNMENT,
                f"{role.value} 没有指定待处理问题",
            )
        for issue_id in issue_ids:
            issue = session.issues.get(issue_id)
            if issue is None:
                return (ReasonCode.UNKNOWN_ISSUE, f"问题 {issue_id} 不存在")
            if issue.needs_user:
                return (
                    ReasonCode.ISSUE_NEEDS_USER,
                    f"问题 {issue_id} 需要用户裁决，不能点名角色回应",
                )
            if issue.status != IssueStatus.OPEN:
                return (
                    ReasonCode.ISSUE_ALREADY_CLOSED,
                    f"问题 {issue_id} 已处理，不能重复点名",
                )
            if issue.input_version != session.input_version:
                return (
                    ReasonCode.STALE_INPUT_VERSION,
                    f"问题 {issue_id} 属于旧输入版本，请在当前版本重新提出",
                )
            if issue.assignee is not None and issue.assignee != role:
                return (
                    ReasonCode.ISSUE_ALREADY_ASSIGNED,
                    f"问题 {issue_id} 已指派给 {issue.assignee.value}",
                )
            if issue.topic_id != item.topic_id:
                return (
                    ReasonCode.INVALID_ISSUE,
                    f"问题 {issue_id} 属于议题 {issue.topic_id}，与当前议题不一致",
                )
    return None


# ------------------------------------------------------------------- 成稿


def missing_statements(session: DiscussionSession) -> dict[str, tuple[Role, ...]]:
    """按议题返回尚未发言的必需角色。"""

    missing: dict[str, tuple[Role, ...]] = {}
    if session.agenda is None:
        return missing
    for item in session.agenda.items:
        covered = session.covered_statements.get(item.topic_id, set())
        gap = tuple(role for role in item.required_roles if role not in covered)
        if gap:
            missing[item.topic_id] = gap
    return missing


def unchallenged_topics(session: DiscussionSession) -> tuple[str, ...]:
    if session.agenda is None:
        return ()
    return tuple(
        item.topic_id
        for item in session.agenda.items
        if item.topic_id not in session.challenged_topics
    )


def draft_blockers(session: DiscussionSession) -> tuple[ReasonCode, ...]:
    """候选稿成稿门禁：必需发言、质疑、阻断问题三者齐备。"""

    blockers: list[ReasonCode] = []
    if missing_statements(session):
        blockers.append(ReasonCode.MISSING_REQUIRED_SPEAKERS)
    if unchallenged_topics(session):
        blockers.append(ReasonCode.CHALLENGE_MISSING)
    if any(
        issue.status == IssueStatus.OPEN
        and issue.severity == Severity.BLOCKING
        and issue.input_version == session.input_version
        for issue in session.issues.values()
    ):
        blockers.append(ReasonCode.OPEN_BLOCKING_ISSUES)
    return tuple(blockers)


def blocking_issue_ids(session: DiscussionSession) -> tuple[str, ...]:
    return tuple(
        issue.issue_id
        for issue in session.issues.values()
        if issue.status == IssueStatus.OPEN
        and issue.severity == Severity.BLOCKING
        and issue.input_version == session.input_version
    )


def describe_issue(issue: Issue) -> str:
    owner = issue.assignee.value if issue.assignee else "未指派"
    return f"{issue.issue_id}（{issue.severity.value}/{owner}）：{issue.summary}"
