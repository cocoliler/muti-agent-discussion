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
    "retry",
    "run_agents",
    "abandon_task",
    "require_changes",
    "submit_output",
    "submit_review",
    "submit_agenda",
    "propose",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class CreateDiscussionRequest(_Strict):
    idea: str = Field(min_length=1, max_length=4000)


class ClarificationAnswerModel(_Strict):
    question: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=8000)
    options: list[str] = Field(default_factory=list, max_length=4)


class PostMessageRequest(_Strict):
    text: str = Field(min_length=1, max_length=8000)
    reply_to_seq: int | None = Field(default=None, ge=1)
    clarification_answers: list[ClarificationAnswerModel] = Field(default_factory=list, max_length=50)
    replaces_answer_seq: int | None = Field(default=None, ge=1)
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
    plain_summary: str | None = None
    topic_id: str | None = None
    task_id: str | None = None
    reply_to_seq: int | None = None
    source_seqs: list[int] = Field(default_factory=list)
    item_refs: list[str] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    clarification_answers: list[ClarificationAnswerModel] = Field(default_factory=list)
    replaces_answer_seq: int | None = None
    substantive: bool = True
    basis: str | None = None
    verification: str | None = None
    agreements: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    input_version: int
    round_no: int
    created_at: str | None = None


class ParticipantView(_Strict):
    role: str
    status: str
    attempt_no: int | None = None
    error_code: str | None = None
    diagnostic: str | None = None


class DraftSummary(_Strict):
    draft_id: str
    version: int
    input_version: int
    open_issue_ids: list[str] = Field(default_factory=list)
    review_verdict: str | None = None
    delivery_status: Literal["pending_review", "invalid", "blocked_review", "ready"] | None = None
    delivery_errors: list[dict[str, Any]] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)
    #: 记录员保留的共识、备选项与未收敛分歧，仅用于展示，不进入导出正文。
    agreements: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    #: 本版本依据的评分卡与被采纳方案，未打分时为空。
    scorecard_id: str | None = None
    chosen_option_id: str | None = None
    parent_id: str | None = None


class DraftHistoryItem(_Strict):
    draft_id: str
    version: int
    input_version: int
    current: bool = False
    signed: bool = False
    open_issue_ids: list[str] = Field(default_factory=list)


class ReviewSummary(_Strict):
    review_id: str
    draft_id: str
    input_version: int
    verdict: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    verified_findings: list[dict[str, Any]] = Field(default_factory=list)


class ScoredOptionView(_Strict):
    option_id: str
    title: str
    scores: dict[str, int] = Field(default_factory=dict)
    total: float
    rank: int


class OptionStanceView(_Strict):
    option_id: str
    position: str
    reason: str
    source_seqs: list[int] = Field(default_factory=list)
    score_dimension: str | None = None


class ScorecardView(_Strict):
    scorecard_id: str
    topic_id: str
    input_version: int
    chosen_option_id: str
    stances: dict[str, list[OptionStanceView]] = Field(default_factory=dict)
    rationale: str = ""
    weights: dict[str, float] = Field(default_factory=dict)
    options: list[ScoredOptionView] = Field(default_factory=list)


class DecisionView(_Strict):
    ok: bool
    code: str
    message: str = ""
    waiting_for: str | None = None
    seq: int | None = None


class ClarificationView(_Strict):
    message_seq: int
    questions: list[str] = Field(default_factory=list)
    options: dict[str, list[str]] = Field(default_factory=dict)


class UsageTotals(_Strict):
    calls: int = 0
    tokens: int = 0
    elapsed_seconds: float = 0
    retries: int = 0
    invalid_outputs: int = 0


class SessionUsage(UsageTotals):
    by_task: dict[str, UsageTotals] = Field(default_factory=dict)


class DraftDetail(DraftSummary):
    scorecard: ScorecardView | None = None


class DiscussionListItem(_Strict):
    id: str
    project_id: str
    status: str
    round_no: int
    updated_at: str
    title: str


class DiscussionList(_Strict):
    items: list[DiscussionListItem]
    next_cursor: str | None = None


class SessionView(_Strict):
    export_url: str | None = None
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
    clarification: ClarificationView | None = None
    clarification_answers: dict[int, list[ClarificationAnswerModel]] = Field(default_factory=dict)
    superseded_answer_seqs: list[int] = Field(default_factory=list)
    participants: list[ParticipantView] = Field(default_factory=list)
    pending_tasks: list[dict[str, Any]] = Field(default_factory=list)
    agent_mode: str = "simulation"
    usage: SessionUsage = Field(default_factory=SessionUsage)
    active_draft: DraftDetail | None = None
    scorecard: ScorecardView | None = None
    drafts: list[DraftHistoryItem] = Field(default_factory=list)
    reviews: list[ReviewSummary] = Field(default_factory=list)
    open_issues: list[dict[str, Any]] = Field(default_factory=list)


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
    review_verdict: str | None = None
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
