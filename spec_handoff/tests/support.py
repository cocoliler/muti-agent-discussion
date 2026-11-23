"""测试夹具：一份可签收的草稿与会话快照。"""

from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from discussion_orchestration import (
    DiscussionSession,
    DraftRevision,
    Issue,
    IssueStatus,
    Message,
    MessageKind,
    ReviewFinding,
    ReviewResult,
    Role,
    SessionStatus,
    Severity,
    Verdict,
)

BASE_DRAFT: dict[str, Any] = {
    "product_name": "内部会议室预约",
    "background": "员工通过聊天工具登记会议室，前台另有访客表格，容易出现重复占用。",
    "goals": ["员工可查询空闲时段并完成预约", "同一房间的重叠时段最多一笔有效占用"],
    "target_users": ["发起会议的员工", "审批的行政人员"],
    "requirements": [
        {
            "id": "FR-01",
            "kind": "functional",
            "priority": "must",
            "text": "员工可按时间与容量筛选可用会议室并创建预约。",
            "acceptance_criteria": ["选择可用时段后返回确认记录"],
            "source_seqs": [2],
            "covers": ["P-1"],
        },
        {
            "id": "FR-02",
            "kind": "functional",
            "priority": "must",
            "text": "同一会议室的重叠时段最多只有一笔有效占用。",
            "acceptance_criteria": ["两个并发请求只有一个成功，另一个收到冲突提示"],
            "source_seqs": [2],
            "covers": ["R-1"],
        },
    ],
    "technical_constraints": ["预约占用结果由服务端保证"],
    "development_constraints": ["第一阶段只在一个园区试点"],
    "delivery_requirements": ["交付员工预约界面与冲突测试"],
    "acceptance_criteria": ["现场演示并发预约只有一个成功"],
    "supplemental_text": "演示用规格。",
    "confirmed_assumptions": ["试点园区接受待审批预约暂占 30 分钟"],
    "pain_points": [{"id": "P-1", "text": "员工看不到房间是否已被占用", "confirmed": True}],
    "risks": [{"id": "R-1", "text": "并发写入可能双重占用", "must_handle": True}],
    "removed_requirement_ids": [],
}

MESSAGES = (
    Message(
        seq=1,
        role=Role.USER,
        kind=MessageKind.USER_INPUT,
        text="做一个内部会议室预约工具，首版先解决查空闲和预约。",
        input_version=1,
        round_no=0,
    ),
    Message(
        seq=2,
        role=Role.ARCHITECT,
        kind=MessageKind.STATEMENT,
        text="预约服务需要处理同一会议室的时段重叠。",
        input_version=1,
        round_no=1,
    ),
)


def make_draft(**overrides: Any) -> dict[str, Any]:
    payload = copy.deepcopy(BASE_DRAFT)
    payload.update(copy.deepcopy(overrides))
    return payload


def with_requirement(index: int, **changes: Any) -> dict[str, Any]:
    payload = copy.deepcopy(BASE_DRAFT)
    payload["requirements"][index].update(changes)
    return payload


def make_session(
    content: Mapping[str, Any] | None = None,
    *,
    status: SessionStatus = SessionStatus.AWAITING_CONFIRMATION,
    verdict: Verdict = Verdict.PASS,
    findings: Iterable[ReviewFinding] = (),
    draft_id: str = "s-1-v1",
    review_id: str = "rv-1",
    input_version: int = 1,
    issues: Mapping[str, Issue] | None = None,
    parent: Mapping[str, Any] | None = None,
) -> DiscussionSession:
    """构造一份已冻结候选稿的会话快照，直接进入签收门禁所需状态。"""

    session = DiscussionSession(session_id="s-1", project_id="p-1")
    session.status = status
    session.input_version = input_version
    session.messages = list(MESSAGES)

    if parent is not None:
        session.drafts.append(
            DraftRevision(
                draft_id="s-1-v0",
                version=1,
                input_version=1,
                content=parent,
                current=False,
            )
        )

    session.drafts.append(
        DraftRevision(
            draft_id=draft_id,
            version=len(session.drafts) + 1,
            input_version=1,
            content=dict(content if content is not None else make_draft()),
            source_seqs=(2,),
            parent_id="s-1-v0" if parent is not None else None,
            review_id=review_id if verdict is Verdict.PASS else None,
        )
    )
    session.reviews.append(
        ReviewResult(
            review_id=review_id,
            session_id="s-1",
            draft_id=draft_id,
            input_version=1,
            verdict=verdict,
            findings=tuple(findings),
        )
    )
    for identifier, issue in (issues or {}).items():
        session.issues[identifier] = issue
    return session


def blocking_issue(identifier: str = "I-1", *, input_version: int = 1) -> Issue:
    return Issue(
        issue_id=identifier,
        topic_id="T1",
        raised_by=Role.REVIEWER,
        summary="并发重试可能重复授权",
        severity=Severity.BLOCKING,
        input_version=input_version,
        status=IssueStatus.OPEN,
    )


def blocking_finding(identifier: str = "F-1") -> ReviewFinding:
    return ReviewFinding(
        finding_id=identifier,
        severity=Severity.BLOCKING,
        target_item="FR-02",
        summary="验收条件没有说明另一个请求的结果",
        close_condition="补充冲突提示的可观察结果",
        responsible_role=Role.ARCHITECT,
    )
