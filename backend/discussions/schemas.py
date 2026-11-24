"""HTTP 出入参模型（实施方案 §7）。

**字段定稿时只需改本文件**：路由层与存储层都不直接持有这些字段名。
所有模型 ``extra="forbid"``，未知字段由 FastAPI 直接 422。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ActionType = Literal[
    "pause",
    "resume",
    "cancel",
    "extend_quota",
    "retry",
    "abandon_task",
    "require_changes",
    "resolve_issues",
    "submit_output",
    "submit_review",
    "submit_agenda",
    "propose",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class LimitsModel(_Strict):
    max_total_rounds: int = Field(default=12, ge=1, le=100)
    max_directed_rounds: int = Field(default=2, ge=0, le=20)
    max_revision_rounds: int = Field(default=2, ge=0, le=20)
    max_naming_passes_per_round: int = Field(default=2, ge=0, le=20)
    max_dispatch_units: int = Field(default=64, ge=1, le=1000)


class CreateDiscussionRequest(_Strict):
    idea: str = Field(min_length=1, max_length=4000)
    limits: LimitsModel | None = None


class PostMessageRequest(_Strict):
    text: str = Field(min_length=1, max_length=8000)
    reply_to_seq: int | None = Field(default=None, ge=1)
    substantive: bool = True
    #: 客户端看到的上下文版本（§6「接收上下文版本」）；缺省视为不校验
    input_version: int | None = Field(default=None, ge=1)
    expected_state_version: int = Field(ge=1)


class ActionRequest(_Strict):
    type: ActionType
    expected_state_version: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ConfirmRequest(_Strict):
    draft_id: str = Field(min_length=1)
    input_version: int = Field(ge=1)
    expected_state_version: int = Field(ge=1)
    #: §6.2 规格期望版本：首次保存 0，后续用读取到的目标版本
    spec_expected_revision: int = Field(default=0, ge=0)


class MessageView(_Strict):
    seq: int
    role: str
    kind: str
    text: str
    topic_id: str | None = None
    task_id: str | None = None
    reply_to_seq: int | None = None
    source_seqs: list[int] = Field(default_factory=list)
    item_refs: list[str] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    input_version: int
    round_no: int
    created_at: str | None = None


class ParticipantView(_Strict):
    role: str
    status: str
    attempt_no: int | None = None


class DraftSummary(_Strict):
    draft_id: str
    version: int
    input_version: int
    open_issue_ids: list[str] = Field(default_factory=list)
    review_verdict: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)


class DraftHistoryItem(_Strict):
    draft_id: str
    version: int
    input_version: int
    current: bool = False
    open_issue_ids: list[str] = Field(default_factory=list)


class ReviewSummary(_Strict):
    review_id: str
    draft_id: str
    input_version: int
    verdict: str
    findings: list[dict[str, Any]] = Field(default_factory=list)


class DecisionView(_Strict):
    ok: bool
    code: str
    message: str = ""
    waiting_for: str | None = None
    seq: int | None = None


class SessionView(_Strict):
    id: str
    project_id: str
    status: str
    input_version: int
    round_no: int
    round_kind: str | None = None
    phase: str | None = None
    topic_id: str | None = None
    waiting_for: str
    last_seq: int
    state_version: int
    participants: list[ParticipantView] = Field(default_factory=list)
    active_draft: DraftSummary | None = None
    drafts: list[DraftHistoryItem] = Field(default_factory=list)
    reviews: list[ReviewSummary] = Field(default_factory=list)
    open_issues: list[dict[str, Any]] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)


class MessagePage(_Strict):
    items: list[MessageView] = Field(default_factory=list)
    has_more: bool = False
    last_seq: int
    next_cursor: str | None = None


class DecisionEnvelope(_Strict):
    decision: DecisionView
    session: SessionView | None = None
    new_messages: list[MessageView] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None


class HandoffView(_Strict):
    status: str
    draft_id: str
    review_id: str
    input_version: int
    spec_expected_revision: int = 0
    confirmed_at: str
    #: spec_handoff 生成的规范化正文哈希，用于比对服务端实际保存的内容
    content_hash: str | None = None
    spec_revision_id: str | None = None
    #: 导出未开启（默认）时为 null
    export_url: str | None = None


class ConfirmEnvelope(_Strict):
    decision: DecisionView
    handoff: HandoffView | None = None
    session: SessionView | None = None


class HistoryView(_Strict):
    drafts: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    handoff: dict[str, Any] | None = None
