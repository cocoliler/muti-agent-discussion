"""讨论编排器使用的枚举与角色分组。

编排层不调用模型、不访问数据库、不依赖 Web 框架：它只消费角色输出信封，
返回可派发的角色任务。
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """群聊参与者。"""

    USER = "user"
    HOST = "host"
    PRODUCT = "product"
    RESEARCH = "research"
    ARCHITECT = "architect"
    DESIGNER = "designer"
    BUSINESS = "business"
    CHALLENGER = "challenger"
    RECORDER = "recorder"
    REVIEWER = "reviewer"


#: 首轮并行发言的专业角色。
PROFESSIONAL_ROLES: tuple[Role, ...] = (
    Role.PRODUCT,
    Role.RESEARCH,
    Role.ARCHITECT,
    Role.DESIGNER,
    Role.BUSINESS,
)

#: 每个形成新共识的回合都必须参与的固定角色。
CONSENSUS_ROLES: tuple[Role, ...] = (Role.CHALLENGER, Role.RECORDER)

#: 允许被主持人点名的角色，主持人与独立评审不参与点名回应。
NAMABLE_ROLES: frozenset[Role] = frozenset(PROFESSIONAL_ROLES)


class SessionStatus(str, Enum):
    """会话阶段。"""

    CLARIFYING = "clarifying"
    DISCUSSING = "discussing"
    REVIEWING = "reviewing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELED = "canceled"


class RoundKind(str, Enum):
    """回合类型。"""

    FIRST = "first"
    DIRECTED = "directed"
    REVISION = "revision"


class Phase(str, Enum):
    """回合内部阶段。"""

    STATEMENTS = "statements"
    CHALLENGE = "challenge"
    RESPONSES = "responses"
    INTEGRATION = "integration"
    DRAFTING = "drafting"


class TaskKind(str, Enum):
    """派发给角色的任务类型。"""

    STATEMENT = "statement"
    CHALLENGE = "challenge"
    RESPONSE = "response"
    INTEGRATION = "integration"
    DRAFT = "draft"
    #: 权衡分歧的收敛打分，由主持人按维度给各方案打分。
    SCORING = "scoring"


class MessageKind(str, Enum):
    """群聊消息类型。"""

    USER_INPUT = "user_input"
    AGENDA = "agenda"
    CLARIFICATION = "clarification"
    STATEMENT = "statement"
    CHALLENGE = "challenge"
    RESPONSE = "response"
    INTEGRATION = "integration"
    DRAFT = "draft"
    REVIEW = "review"
    DECISION = "decision"
    SYSTEM = "system"


class Severity(str, Enum):
    """问题严重度。"""

    BLOCKING = "blocking"
    MAJOR = "major"
    MINOR = "minor"


class DivergenceKind(str, Enum):
    """分歧类型：决定用哪种方式收敛。"""

    #: 事实分歧：缺证据，靠调研/引用补齐。
    FACT = "fact"
    #: 价值分歧：取舍标准不唯一，必须由用户裁决。
    VALUE = "value"
    #: 权衡分歧：各有取舍，按评分维度比较。
    TRADEOFF = "tradeoff"


class ScoreDimension(str, Enum):
    """收敛打分的五个维度。每项 0-5 分，统一按「越高越好」记分。"""

    USER_VALUE = "user_value"
    FEASIBILITY = "feasibility"
    COST = "cost"
    DIFFERENTIATION = "differentiation"
    RISK = "risk"


#: 默认权重：用户价值优先，其次可行性。成本/风险按「可控程度」记分，因此正向加权。
DEFAULT_SCORE_WEIGHTS: dict[ScoreDimension, float] = {
    ScoreDimension.USER_VALUE: 0.30,
    ScoreDimension.FEASIBILITY: 0.25,
    ScoreDimension.COST: 0.15,
    ScoreDimension.DIFFERENTIATION: 0.15,
    ScoreDimension.RISK: 0.15,
}


#: 定向补充轮硬上限：超过后只能提交候选稿或请用户补充。
MAX_DIRECTED_ROUNDS = 2
#: 评审退回修订硬上限：超过后转人工裁决，避免无限循环。
MAX_REVISION_ROUNDS = 2


class IssueStatus(str, Enum):
    """问题处理状态。"""

    OPEN = "open"
    ANSWERED = "answered"
    USER_DECIDED = "user_decided"
    CLOSED = "closed"


class Verdict(str, Enum):
    """独立评审结论。"""

    PASS = "pass"
    REVISE = "revise"
    NEEDS_INPUT = "needs_input"


class HostAction(str, Enum):
    """主持人调度建议的动作。"""

    ASK_USER = "ask_user"
    NAME_RESPONSES = "name_responses"
    START_DIRECTED_ROUND = "start_directed_round"
    SUBMIT_DRAFT = "submit_draft"
    NEXT_TOPIC = "next_topic"


class WaitingFor(str, Enum):
    """当前等待对象。"""

    HOST_AGENDA = "host_agenda"
    HOST_DECISION = "host_decision"
    ROLE_RESULTS = "role_results"
    USER_INPUT = "user_input"
    REVIEW = "review"
    NONE = "none"


class ReasonCode(str, Enum):
    """编排决策结果码。可诊断、可测试、可直接映射到接口错误码。"""

    OK = "ok"
    INVALID_STATUS = "invalid_status"
    INVALID_TRANSITION = "invalid_transition"
    INVALID_AGENDA = "invalid_agenda"
    INVALID_TOPIC = "invalid_topic"
    INVALID_ISSUE = "invalid_issue"
    INVALID_ASSIGNMENT = "invalid_assignment"
    INVALID_TARGET_ROLE = "invalid_target_role"
    AWAITING_USER_INPUT = "awaiting_user_input"
    AWAITING_HOST_DECISION = "awaiting_host_decision"
    AWAITING_REVIEW = "awaiting_review"
    UNKNOWN_TASK = "unknown_task"
    CROSS_SESSION_RESULT = "cross_session_result"
    CROSS_SESSION_REFERENCE = "cross_session_reference"
    CONTRACT_MISMATCH = "contract_mismatch"
    STALE_INPUT_VERSION = "stale_input_version"
    STALE_ROUND = "stale_round"
    ILLEGAL_REPLY_TARGET = "illegal_reply_target"
    EMPTY_OUTPUT = "empty_output"
    CHALLENGE_NEEDS_ISSUE = "challenge_needs_issue"
    CHALLENGE_NEEDS_REFERENCE = "challenge_needs_reference"
    NOT_APPLICABLE_NOT_ALLOWED = "not_applicable_not_allowed"
    REASON_REQUIRED = "reason_required"
    MISSING_EVIDENCE = "missing_evidence"
    MISSING_REQUIRED_SPEAKERS = "missing_required_speakers"
    CHALLENGE_MISSING = "challenge_missing"
    OPEN_BLOCKING_ISSUES = "open_blocking_issues"
    DRAFT_NOT_READY = "draft_not_ready"
    DRAFT_CONTENT_MISSING = "draft_content_missing"
    PENDING_TASKS = "pending_tasks"
    ROUND_LIMIT = "round_limit"
    VALUE_DIVERGENCE_UNRESOLVED = "value_divergence_unresolved"
    SCORING_REQUIRED = "scoring_required"
    INVALID_SCORECARD = "invalid_scorecard"
    UNKNOWN_ISSUE = "unknown_issue"
    ISSUE_NEEDS_USER = "issue_needs_user"
    ISSUE_ALREADY_ASSIGNED = "issue_already_assigned"
    ISSUE_ALREADY_CLOSED = "issue_already_closed"
    STALE_DRAFT = "stale_draft"
    REVIEW_STALE = "review_stale"
    REVIEW_PASS_WITH_BLOCKING = "review_pass_with_blocking"
    REVISE_WITHOUT_RESPONSIBLE_ROLE = "revise_without_responsible_role"
    REVIEW_NOT_PASSED = "review_not_passed"
    LAST_TOPIC = "last_topic"
    SESSION_TERMINATED = "session_terminated"
