"""讨论编排的数据模型。

字段命名与《FlowStudio 多 Agent 群聊与设计规格生成》第 5 节保持一致，
可直接映射到 DiscussionSession / DiscussionMessage / RoleAttempt /
DraftRevision / ReviewResult 等持久化记录。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .enums import (
    PROFESSIONAL_ROLES,
    DivergenceKind,
    ScoreDimension,
    HostAction,
    IssueStatus,
    MessageKind,
    Phase,
    ReasonCode,
    Role,
    RoundKind,
    SessionStatus,
    Severity,
    TaskKind,
    Verdict,
    WaitingFor,
)


@dataclass(frozen=True)
class Assumption:
    """主持人标注的可暂用假设：来源、影响、验证方式。"""

    text: str
    source: str
    impact: str
    verification: str


@dataclass(frozen=True)
class AgendaItem:
    """一个议题及其派发范围。"""

    topic_id: str
    title: str
    question: str
    required_roles: tuple[Role, ...] = PROFESSIONAL_ROLES
    role_scope: tuple[Role, ...] = PROFESSIONAL_ROLES
    assumptions: tuple[Assumption, ...] = ()
    draft_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class InitialOutline:
    product_name: str
    positioning: str
    target_users: tuple[str, ...]
    core_modules: tuple[str, ...]
    business_flow: tuple[str, ...]
    system_boundary: tuple[str, ...]
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()
    scenarios: tuple[str, ...] = ()
    scope_in: tuple[str, ...] = ()
    scope_out: tuple[str, ...] = ()
    module_relations: tuple[str, ...] = ()
    external_dependencies: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Agenda:
    """主持人提交的议题调度建议。"""

    summary: str
    items: tuple[AgendaItem, ...]
    missing_facts: tuple[str, ...] = ()
    question_options: dict[str, tuple[str, ...]] = field(default_factory=dict)
    plain_summary: str | None = None
    initial_outline: InitialOutline | None = None
    outline_source_seqs: tuple[int, ...] = ()
    working_draft: DraftRevision | None = None
    initial_questions: tuple[Mapping[str, Any], ...] = ()
    asked_initial_ids: tuple[str, ...] = ()
    initial_pause_reason: str | None = None

    def item(self, topic_id: str) -> AgendaItem:
        for item in self.items:
            if item.topic_id == topic_id:
                return item
        raise KeyError(topic_id)


@dataclass(frozen=True)
class ClarificationAnswer:
    question: str
    answer: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Message:
    """已提交的群聊消息。序号在会话内单调递增。"""

    seq: int
    role: Role
    kind: MessageKind
    text: str
    input_version: int
    round_no: int
    topic_id: str | None = None
    reply_to_seq: int | None = None
    source_seqs: tuple[int, ...] = ()
    item_refs: tuple[str, ...] = ()
    task_id: str | None = None
    issue_ids: tuple[str, ...] = ()
    clarification_questions: tuple[str, ...] = ()
    clarification_answers: tuple[ClarificationAnswer, ...] = ()
    replaces_answer_seq: int | None = None
    substantive: bool = True
    basis: str | None = None
    verification: str | None = None
    agreements: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    disagreements: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    plain_summary: str | None = None
    draft_suggestions: tuple[Mapping[str, Any], ...] = ()
    rejected_working_revision: Mapping[str, Any] | None = None
    rejected_working_revision: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class IssueResponse:
    issue_id: str
    treatment: str
    verification: str
    source_seqs: tuple[int, ...] = ()


@dataclass
class Issue:
    """待处理问题。ID 跨修订稳定，关闭阻断问题需证据。"""

    issue_id: str
    topic_id: str
    raised_by: Role
    summary: str
    severity: Severity
    #: 分歧类型：价值分歧不能由角色互相说服，必须交用户裁决。
    kind: DivergenceKind = DivergenceKind.FACT
    source_seqs: tuple[int, ...] = ()
    assignee: Role | None = None
    needs_user: bool = False
    user_question: str | None = None
    status: IssueStatus = IssueStatus.OPEN
    closed_by_seq: int | None = None
    response: IssueResponse | None = None
    response_seq: int | None = None
    #: 问题所属的输入版本。输入版本升级后旧问题保留历史，但不再计入当前成稿门禁。
    input_version: int = 1
    closure_condition: str | None = None
    proposed_resolution: str | None = None
    reopen_if: str | None = None
    draft_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class OptionScore:
    """一个备选方案在五个维度上的得分（0-5，越高越好）。"""

    option_id: str
    title: str
    scores: Mapping[ScoreDimension, int]


@dataclass(frozen=True)
class OptionStance:
    option_id: str
    position: str
    reason: str
    source_seqs: tuple[int, ...] = ()
    score_dimension: ScoreDimension | None = None


@dataclass(frozen=True)
class Scorecard:
    """收敛评分卡。权重与排名由编排层计算，模型只提供各维度原始分。"""

    options: tuple[OptionScore, ...]
    scorecard_id: str = ""
    session_id: str = ""
    topic_id: str = ""
    input_version: int = 1
    weights: Mapping[ScoreDimension, float] = field(default_factory=dict)
    rationale: str = ""
    chosen_option_id: str = ""
    stances: dict[Role, tuple[OptionStance, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeakerTask:
    """派发给单个角色的逻辑任务。同一逻辑任务可重试但只合并一份有效结果。"""

    task_id: str
    session_id: str
    role: Role
    kind: TaskKind
    phase: Phase
    round_kind: RoundKind
    round_no: int
    topic_id: str
    snapshot_ref: str
    input_version: int
    message_seqs: tuple[int, ...] = ()
    issue_ids: tuple[str, ...] = ()
    focus_seq: int | None = None
    working_draft_id: str | None = None


@dataclass(frozen=True)
class SpeakerOutput:
    """角色执行结果信封。正文语义由角色输出契约校验，此层只校验编排合法性。"""

    task_id: str
    session_id: str
    role: Role
    kind: TaskKind
    input_version: int
    snapshot_ref: str
    round_no: int
    topic_id: str
    text: str = ""
    reply_to_seq: int | None = None
    source_seqs: tuple[int, ...] = ()
    issue_ids: tuple[str, ...] = ()
    raised_issues: tuple[Issue, ...] = ()
    evidence: tuple[str, ...] = ()
    basis: str | None = None
    verification: str | None = None
    responses: tuple[IssueResponse, ...] = ()
    stances: tuple[OptionStance, ...] = ()
    not_applicable: str | None = None
    draft_content: Mapping[str, Any] | None = None
    item_refs: tuple[str, ...] = ()
    #: 记录员在成稿时保留的分歧记录，随草稿版本落库、供前端与签收展示。
    agreements: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    disagreements: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    #: 评分任务的结果；其他任务为空。
    scorecard: Scorecard | None = None
    plain_summary: str | None = None
    no_new_issues: bool = False
    disposition: str = "suggestion"
    draft_suggestions: tuple[Mapping[str, Any], ...] = ()
    draft_decisions: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class ReviewFinding:
    """评审发现。responsible_role 决定定向修订的派发范围。"""

    finding_id: str
    severity: Severity
    target_item: str
    summary: str
    close_condition: str
    responsible_role: Role | None = None
    issue_id: str | None = None
    source_seqs: tuple[int, ...] = ()


@dataclass(frozen=True)
class ReviewResult:
    """独立评审对冻结草稿的结论。"""

    review_id: str
    session_id: str
    draft_id: str
    input_version: int
    verdict: Verdict
    findings: tuple[ReviewFinding, ...] = ()
    questions: tuple[str, ...] = ()
    question_options: dict[str, tuple[str, ...]] = field(default_factory=dict)
    verified_findings: tuple[ReviewFinding, ...] = ()
    plain_summary: str | None = None


@dataclass
class DraftRevision:
    """版本化候选草稿。条目 ID 沿用，内容改动生成新版本。"""

    draft_id: str
    version: int
    input_version: int
    content: Mapping[str, Any]
    source_seqs: tuple[int, ...] = ()
    parent_id: str | None = None
    current: bool = True
    review_id: str | None = None
    open_issue_ids: tuple[str, ...] = ()
    #: 成稿时记录员保留的共识、备选项与未收敛分歧。导出时不含这些字段。
    agreements: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    disagreements: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    #: 本版本依据的评分卡与被采纳的方案，用于追溯「为什么这么定」。
    scorecard_id: str | None = None
    chosen_option_id: str | None = None
    working_schema_version: int | None = None
    quality_gate_result: Mapping[str, Any] | None = None
    publication_seq: int | None = None
    working_changes: Mapping[str, Any] | None = None
    based_on_working_draft_id: str | None = None


@dataclass(frozen=True)
class Rejection:
    """被拒绝的角色结果。留在历史里可诊断，不推进会话。"""

    task_id: str
    code: ReasonCode
    note: str
    input_version: int
    round_no: int


@dataclass(frozen=True)
class HostProposal:
    """主持人调度建议卡片：动作、议题、目标角色、待处理问题、理由。"""

    action: HostAction
    reason: str = ""
    topic_id: str | None = None
    assignments: Mapping[Role, tuple[str, ...]] | None = None
    questions: tuple[str, ...] = ()
    issue_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    """编排决策结果。ok=False 时状态不变，附带可诊断原因。"""

    ok: bool
    code: ReasonCode
    message: str = ""
    tasks: tuple[SpeakerTask, ...] = ()
    seq: int | None = None
    status: SessionStatus | None = None
    waiting_for: WaitingFor | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class DiscussionSession:
    """讨论会话的编排状态。持久化字段与设计文档第 5 节对应。"""

    session_id: str
    project_id: str
    status: SessionStatus = SessionStatus.CLARIFYING
    input_version: int = 1
    snapshot_ref: str = "snap-v1"
    round_no: int = 0
    round_kind: RoundKind | None = None
    phase: Phase | None = None
    topic_id: str | None = None
    waiting_for: WaitingFor = WaitingFor.HOST_AGENDA
    agenda: Agenda | None = None
    messages: list[Message] = field(default_factory=list)
    issues: dict[str, Issue] = field(default_factory=dict)
    named_responses: dict[Role, tuple[str, ...]] = field(default_factory=dict)
    tasks: dict[str, SpeakerTask] = field(default_factory=dict)
    stale_tasks: dict[str, SpeakerTask] = field(default_factory=dict)
    round_results: dict[str, SpeakerOutput] = field(default_factory=dict)
    covered_statements: dict[str, set[Role]] = field(default_factory=dict)
    challenged_topics: set[str] = field(default_factory=set)
    closed_topics: set[str] = field(default_factory=set)
    drafts: list[DraftRevision] = field(default_factory=list)
    # Working artifact for this input version. Only frozen candidates belong in drafts.
    initial_draft: DraftRevision | None = None
    previous_outlines: list[DraftRevision] = field(default_factory=list)
    initial_question_ids: tuple[str, ...] = ()
    reviews: list[ReviewResult] = field(default_factory=list)
    #: 每个议题的收敛评分卡（按输入版本），以及最终采纳的方案。
    scorecards: list[Scorecard] = field(default_factory=list)
    chosen_option_id: str | None = None
    rejections: list[Rejection] = field(default_factory=list)
    failures: dict[str, list[str]] = field(default_factory=dict)
    directed_rounds: int = 0
    revision_rounds: int = 0
    #: 可选的单个输入版本内的轮次保护。
    max_directed_rounds: int | None = None
    max_revision_rounds: int | None = None
    dispatch_units: int = 0
    round_challenged: bool = False
    paused_from: SessionStatus | None = None
    paused_waiting_for: WaitingFor | None = None

    # ------------------------------------------------------------------ 查询
    @property
    def last_seq(self) -> int:
        return self.messages[-1].seq if self.messages else 0

    def next_seq(self) -> int:
        return self.last_seq + 1

    @property
    def current_draft(self) -> DraftRevision | None:
        for draft in reversed(self.drafts):
            if draft.current:
                return draft
        return None

    def current_item(self) -> AgendaItem | None:
        if self.agenda is None or self.topic_id is None:
            return None
        try:
            return self.agenda.item(self.topic_id)
        except KeyError:
            return None

    def review_roles(self) -> tuple[Role, ...]:
        """评审可向各议题的专业角色派发整改，旧议题沿用存档中的名单。"""
        if self.agenda is None:
            return PROFESSIONAL_ROLES
        return tuple(dict.fromkeys(role for item in self.agenda.items for role in item.role_scope))

    def open_issues(self, topic_id: str | None = None) -> tuple[Issue, ...]:
        return tuple(
            issue
            for issue in self.issues.values()
            if issue.status == IssueStatus.OPEN
            and issue.input_version == self.input_version
            and (topic_id is None or issue.topic_id == topic_id)
        )

    def blocking_issues(self) -> tuple[Issue, ...]:
        return tuple(
            issue
            for issue in self.issues.values()
            if issue.status == IssueStatus.OPEN
            and issue.severity == Severity.BLOCKING
            and issue.input_version == self.input_version
        )

    def messages_of_round(self) -> tuple[Message, ...]:
        return tuple(m for m in self.messages if m.round_no == self.round_no)

    def review_of(self, draft_id: str) -> ReviewResult | None:
        for review in reversed(self.reviews):
            if review.draft_id == draft_id:
                return review
        return None
