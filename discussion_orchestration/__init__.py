"""多 Agent 讨论的确定性编排内核。

用法概览::

    from discussion_orchestration import DiscussionEngine, Agenda, AgendaItem

    engine = DiscussionEngine()
    session = engine.create_session(session_id="s-1", project_id="p-1", idea="做一个会议室预约工具")
    decision = engine.submit_agenda(session, Agenda(summary="...", items=(AgendaItem(...),)))
    for task in decision.tasks:            # 首轮并行派发
        ...

编排器不调用模型、不写库，只把「能不能推进、下一步派给谁」变成可测的决策。
"""

from .engine import DiscussionEngine
from .enums import (
    DEFAULT_SCORE_WEIGHTS,
    MAX_DIRECTED_ROUNDS,
    MAX_REVISION_ROUNDS,
    NAMABLE_ROLES,
    PROFESSIONAL_ROLES,
    DivergenceKind,
    HostAction,
    IssueStatus,
    MessageKind,
    Phase,
    ReasonCode,
    Role,
    RoundKind,
    ScoreDimension,
    SessionStatus,
    Severity,
    TaskKind,
    Verdict,
    WaitingFor,
)
from .models import (
    Agenda,
    AgendaItem,
    Assumption,
    Decision,
    DiscussionSession,
    DraftRevision,
    HostProposal,
    Issue,
    Message,
    OptionScore,
    Rejection,
    ReviewFinding,
    ReviewResult,
    Scorecard,
    SpeakerOutput,
    SpeakerTask,
)
from .state_machine import (
    ALLOWED_TRANSITIONS,
    IllegalTransition,
    assert_transition,
    can_resume,
    can_transition,
    is_terminal,
)
from .validation import draft_blockers, missing_statements, validate_agenda

__all__ = [
    "ALLOWED_TRANSITIONS",
    "Agenda",
    "AgendaItem",
    "Assumption",
    "DEFAULT_SCORE_WEIGHTS",
    "Decision",
    "DiscussionEngine",
    "DiscussionSession",
    "DivergenceKind",
    "DraftRevision",
    "HostAction",
    "HostProposal",
    "IllegalTransition",
    "Issue",
    "IssueStatus",
    "MAX_DIRECTED_ROUNDS",
    "MAX_REVISION_ROUNDS",
    "Message",
    "MessageKind",
    "NAMABLE_ROLES",
    "OptionScore",
    "PROFESSIONAL_ROLES",
    "Phase",
    "ReasonCode",
    "Rejection",
    "ReviewFinding",
    "ReviewResult",
    "Role",
    "RoundKind",
    "ScoreDimension",
    "Scorecard",
    "SessionStatus",
    "Severity",
    "SpeakerOutput",
    "SpeakerTask",
    "TaskKind",
    "Verdict",
    "WaitingFor",
    "assert_transition",
    "can_resume",
    "can_transition",
    "draft_blockers",
    "is_terminal",
    "missing_statements",
    "validate_agenda",
]
