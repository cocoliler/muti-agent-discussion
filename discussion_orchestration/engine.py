"""讨论编排引擎。

本层负责：
- 会话状态机与阶段迁移校验；
- 议题调度：首个议题激活、逐议题推进、议题切换；
- 首轮并行派发、质疑、点名回应、记录员整合；
- 人工澄清（暂停等用户补充）与定向修订（评审退回只调度相关角色）；
- 输入版本与快照一致性校验。

不在本层：角色正文生成与模型调用、HTTP 与持久化、界面渲染、规格契约校验与导出。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Mapping

from .enums import (
    NAMABLE_ROLES,
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
    ClarificationAnswer,
    DiscussionSession,
    Decision,
    DraftRevision,
    HostProposal,
    Issue,
    IssueResponse,
    Message,
    Rejection,
    ReviewResult,
    Scorecard,
    SpeakerOutput,
    SpeakerTask,
)
from .scoring import normalize_weights, rank_options
from .scoring import validate as scorecard_problem
from .state_machine import assert_transition, can_pause, can_resume, is_terminal
from .validation import (
    blocking_issue_ids,
    citation_problem,
    draft_blockers,
    known_seqs,
    missing_statements,
    validate_agenda,
    validate_assignments,
)

_FIRST_PHASE: dict[RoundKind, Phase] = {
    RoundKind.FIRST: Phase.STATEMENTS,
    RoundKind.DIRECTED: Phase.RESPONSES,
    RoundKind.REVISION: Phase.RESPONSES,
}


@dataclass(frozen=True)
class _Dispatch:
    tasks: tuple[SpeakerTask, ...] = ()
    code: ReasonCode = ReasonCode.OK


class DiscussionEngine:
    """纯内存编排器。所有方法只改传入的会话对象，不做 IO。"""

    # ------------------------------------------------------------ 会话与用户
    def create_session(
        self, *, session_id: str, project_id: str, idea: str
    ) -> DiscussionSession:
        session = DiscussionSession(
            session_id=session_id, project_id=project_id
        )
        self._append(session, Role.USER, MessageKind.USER_INPUT, idea)
        return session

    def note_user_input(
        self,
        session: DiscussionSession,
        text: str,
        *,
        reply_to_seq: int | None = None,
        substantive: bool = True,
        clarification_answers: tuple[ClarificationAnswer, ...] = (),
        replaces_answer_seq: int | None = None,
    ) -> Decision:
        """记录用户输入。实质补充升级输入版本并作废在途结果。"""

        if is_terminal(session.status):
            return self._reject(session, ReasonCode.SESSION_TERMINATED, "会话已结束")
        if session.status == SessionStatus.PAUSED:
            return self._reject(
                session, ReasonCode.INVALID_STATUS, "会话已暂停，请先恢复或取消"
            )
        if not text.strip():
            return self._reject(session, ReasonCode.EMPTY_OUTPUT, "用户输入不能为空")
        if reply_to_seq is not None:
            problem = citation_problem(session, (), reply_to_seq)
            if problem:
                return self._reject(session, problem[0], problem[1])

        if clarification_answers or replaces_answer_seq is not None:
            prompt = next((item for item in session.messages if item.seq == reply_to_seq), None)
            if not substantive or prompt is None or prompt.kind != MessageKind.CLARIFICATION:
                return self._reject(session, ReasonCode.ILLEGAL_REPLY_TARGET, "回答需关联原澄清问题")
            if replaces_answer_seq is not None:
                previous = next((item for item in reversed(session.messages)
                                 if item.kind == MessageKind.USER_INPUT and item.substantive
                                 and item.reply_to_seq == prompt.seq), None)
                if previous is None or previous.seq != replaces_answer_seq:
                    return self._reject(session, ReasonCode.ILLEGAL_REPLY_TARGET, "请打开该问题的最新回答重新填写")

        self._append(
            session,
            Role.USER,
            MessageKind.USER_INPUT,
            text,
            reply_to_seq=reply_to_seq,
            clarification_answers=clarification_answers,
            replaces_answer_seq=replaces_answer_seq,
            substantive=substantive,
        )
        if not substantive:
            return self._result(
                session,
                ReasonCode.OK,
                detail={"input_version": session.input_version, "substantive": False},
            )

        superseded = sorted(session.tasks)
        for task in session.tasks.values():
            session.stale_tasks[task.task_id] = task
        session.tasks.clear()
        session.input_version += 1
        session.snapshot_ref = f"snap-v{session.input_version}"
        session.covered_statements.clear()
        session.challenged_topics.clear()
        session.round_results.clear()
        session.directed_rounds = 0
        session.revision_rounds = 0
        session.named_responses.clear()
        session.round_challenged = False
        session.round_kind = None
        session.phase = None
        for draft in session.drafts:
            draft.current = False

        if session.status != SessionStatus.CLARIFYING:
            self._transition(session, SessionStatus.CLARIFYING)
        session.waiting_for = WaitingFor.HOST_AGENDA
        self._append(
            session,
            Role.HOST,
            MessageKind.SYSTEM,
            f"已记录输入版本 v{session.input_version}，等待主持人重建议题快照",
        )
        return self._result(
            session,
            ReasonCode.OK,
            detail={
                "input_version": session.input_version,
                "substantive": True,
                "superseded_tasks": superseded,
            },
        )

    # ------------------------------------------------------------ 议题调度
    def submit_agenda(self, session: DiscussionSession, agenda) -> Decision:
        """主持人提交「动作、议题、目标角色、问题、理由」的调度建议。"""

        if session.status != SessionStatus.CLARIFYING:
            return self._reject(
                session, ReasonCode.INVALID_STATUS, "只有在澄清阶段可以提交或重建议题"
            )
        problems = validate_agenda(agenda)
        if problems:
            return self._reject(session, ReasonCode.INVALID_AGENDA, "；".join(problems))

        session.agenda = agenda
        lead = agenda.items[0]
        self._append(
            session, Role.HOST, MessageKind.AGENDA, agenda.summary, topic_id=lead.topic_id,
            plain_summary=agenda.plain_summary,
        )
        if agenda.missing_facts:
            questions = "；".join(agenda.missing_facts)
            self._append(
                session,
                Role.HOST,
                MessageKind.CLARIFICATION,
                f"需要用户补充关键事实：{questions}",
                topic_id=lead.topic_id,
                clarification_questions=agenda.missing_facts,
            )
            session.waiting_for = WaitingFor.USER_INPUT
            return self._result(
                session,
                ReasonCode.AWAITING_USER_INPUT,
                detail={"missing_facts": list(agenda.missing_facts)},
            )

        self._transition(session, SessionStatus.DISCUSSING)
        self._activate_topic(session, lead)
        return self._begin_round(session, RoundKind.FIRST)

    def move_to_next_topic(self, session: DiscussionSession) -> Decision:
        """当前议题收敛后由主持人推进到下一个议题。"""

        if session.status != SessionStatus.DISCUSSING:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不能切换议题")
        if session.waiting_for != WaitingFor.HOST_DECISION:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前不在等待主持人调度")
        if session.tasks:
            return self._reject(session, ReasonCode.PENDING_TASKS, "仍有角色任务未提交")
        item = session.current_item()
        if item is None:
            return self._reject(session, ReasonCode.INVALID_TOPIC, "当前没有活动议题")
        if missing_statements(session).get(item.topic_id):
            return self._reject(
                session, ReasonCode.MISSING_REQUIRED_SPEAKERS, "当前议题的必需发言未齐备"
            )
        if item.topic_id not in session.challenged_topics:
            return self._reject(session, ReasonCode.CHALLENGE_MISSING, "当前议题缺少质疑")
        tradeoffs = self._open_tradeoffs(session)
        if tradeoffs:
            return self._dispatch_scoring(session, tradeoffs)
        if session.open_issues(item.topic_id):
            return self._reject(
                session, ReasonCode.OPEN_BLOCKING_ISSUES, "当前议题仍有未关闭问题"
            )
        items = session.agenda.items if session.agenda else ()
        index = next(i for i, candidate in enumerate(items) if candidate.topic_id == item.topic_id)
        if index + 1 >= len(items):
            return self._reject(session, ReasonCode.LAST_TOPIC, "已是最后一个议题，可提交候选稿")

        session.closed_topics.add(item.topic_id)
        next_item = items[index + 1]
        self._activate_topic(session, next_item)
        self._append(
            session,
            Role.HOST,
            MessageKind.DECISION,
            f"议题 {item.topic_id} 已收敛，转入 {next_item.topic_id}",
            topic_id=next_item.topic_id,
        )
        return self._begin_round(session, RoundKind.FIRST)

    # ------------------------------------------------------------ 主持人调度
    def propose(self, session: DiscussionSession, proposal: HostProposal) -> Decision:
        """执行主持人调度卡。动作非法或越界时整卡拒绝。"""

        if proposal.topic_id and session.topic_id and proposal.topic_id != session.topic_id:
            return self._reject(
                session, ReasonCode.INVALID_TOPIC, "调度卡指向的议题不是当前议题"
            )
        if proposal.action == HostAction.ASK_USER:
            return self.ask_user(session, proposal.questions, proposal.issue_ids)
        if proposal.action == HostAction.NAME_RESPONSES:
            return self.name_responses(
                session, dict(proposal.assignments or {}), proposal.reason
            )
        if proposal.action == HostAction.START_DIRECTED_ROUND:
            return self.start_directed_round(
                session, dict(proposal.assignments or {}), proposal.reason
            )
        if proposal.action == HostAction.SUBMIT_DRAFT:
            return self.submit_candidate_draft(session)
        if proposal.action == HostAction.NEXT_TOPIC:
            return self.move_to_next_topic(session)
        if proposal.action == HostAction.DECIDE_ROUTINE:
            return self.decide_routine(session, proposal.issue_ids)
        return self._reject(session, ReasonCode.INVALID_ASSIGNMENT, "未知的调度动作")

    def decide_routine(self, session: DiscussionSession, issue_ids: tuple[str, ...]) -> Decision:
        """在当前议题内采用有证据和重开条件的可逆默认方案。"""
        if (session.status != SessionStatus.DISCUSSING or session.waiting_for != WaitingFor.HOST_DECISION or session.topic_id is None
                or session.tasks):
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不能拍板")
        if not issue_ids or len(set(issue_ids)) != len(issue_ids):
            return self._reject(session, ReasonCode.INVALID_ISSUE, "拍板需要唯一的议题问题")
        open_issues = {issue.issue_id: issue for issue in session.open_issues(session.topic_id)}
        issues = [open_issues.get(issue_id) for issue_id in issue_ids]
        if any(issue is None or issue.kind != DivergenceKind.ROUTINE or issue.severity == Severity.BLOCKING
               or issue.needs_user or not issue.source_seqs or not (issue.proposed_resolution or "").strip()
               or not (issue.reopen_if or "").strip() for issue in issues):
            return self._reject(session, ReasonCode.INVALID_ISSUE, "只有当前议题的非阻断可逆问题可由主持人拍板")
        for issue in issues:
            assert issue is not None
            summary = f"本版暂按「{issue.proposed_resolution}」推进。"
            message = self._append(session, Role.HOST, MessageKind.DECISION,
                                   f"{summary}\n依据：{issue.summary}\n重开条件：{issue.reopen_if}",
                                   plain_summary=summary, issue_ids=(issue.issue_id,), source_seqs=issue.source_seqs)
            issue.status = IssueStatus.CLOSED
            issue.closed_by_seq = message.seq
            issue.assignee = Role.HOST
        return self._result(session, ReasonCode.OK, seq=session.last_seq)

    def ask_user(
        self,
        session: DiscussionSession,
        questions: tuple[str, ...],
        issue_ids: tuple[str, ...] = (),
    ) -> Decision:
        """关键事实缺失或需要用户取舍时暂停讨论并发澄清卡。"""

        if session.status != SessionStatus.DISCUSSING:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不能发起澄清")
        if session.tasks:
            return self._reject(session, ReasonCode.PENDING_TASKS, "仍有角色任务未提交")
        if not questions or not all(question.strip() for question in questions):
            return self._reject(session, ReasonCode.REASON_REQUIRED, "澄清问题不能为空")
        for issue_id in issue_ids:
            issue = session.issues.get(issue_id)
            if issue is None:
                return self._reject(session, ReasonCode.UNKNOWN_ISSUE, f"问题 {issue_id} 不存在")
            if issue.status != IssueStatus.OPEN:
                return self._reject(
                    session, ReasonCode.ISSUE_ALREADY_CLOSED, f"问题 {issue_id} 已处理"
                )

        for issue_id in issue_ids:
            session.issues[issue_id].needs_user = True
        self._transition(session, SessionStatus.CLARIFYING)
        self._append(
            session,
            Role.HOST,
            MessageKind.CLARIFICATION,
            "；".join(questions),
            issue_ids=issue_ids,
            source_seqs=tuple(dict.fromkeys(
                seq for issue_id in issue_ids for seq in session.issues[issue_id].source_seqs
            )),
            clarification_questions=questions,
        )
        session.waiting_for = WaitingFor.USER_INPUT
        return self._result(
            session,
            ReasonCode.AWAITING_USER_INPUT,
            detail={"questions": list(questions), "issue_ids": list(issue_ids)},
        )

    def name_responses(
        self,
        session: DiscussionSession,
        assignments: Mapping[Role, tuple[str, ...]],
        reason: str = "",
    ) -> Decision:
        """质疑后点名相关角色定向回应，范围限于议题涉及角色。"""

        if session.status != SessionStatus.DISCUSSING:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不能点名回应")
        if session.waiting_for != WaitingFor.HOST_DECISION:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前不在等待主持人调度")
        if session.tasks:
            return self._reject(session, ReasonCode.PENDING_TASKS, "仍有角色任务未提交")
        problem = validate_assignments(session, assignments)
        if problem:
            return self._reject(session, problem[0], problem[1])

        for role, issue_ids in assignments.items():
            for issue_id in issue_ids:
                session.issues[issue_id].assignee = role
        session.named_responses = {role: tuple(ids) for role, ids in assignments.items()}
        self._append(
            session,
            Role.HOST,
            MessageKind.DECISION,
            reason or "点名回应：" + "，".join(role.value for role in assignments),
            issue_ids=tuple(iid for ids in assignments.values() for iid in ids),
        )
        return self._enter_phase(session, Phase.RESPONSES)

    def start_directed_round(
        self,
        session: DiscussionSession,
        assignments: Mapping[Role, tuple[str, ...]],
        reason: str = "",
    ) -> Decision:
        """开一轮定向补充。"""

        if session.status != SessionStatus.DISCUSSING:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不能开定向补充轮")
        if session.waiting_for != WaitingFor.HOST_DECISION:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前不在等待主持人调度")
        if session.tasks:
            return self._reject(session, ReasonCode.PENDING_TASKS, "仍有角色任务未提交")
        if session.max_directed_rounds is not None and session.directed_rounds >= session.max_directed_rounds:
            return self._reject(
                session,
                ReasonCode.ROUND_LIMIT,
                f"定向补充轮已达上限 {session.max_directed_rounds} 轮，请提交候选稿或请用户补充",
                detail={
                    "directed_rounds": session.directed_rounds,
                    "max_directed_rounds": session.max_directed_rounds,
                },
            )
        problem = validate_assignments(session, assignments)
        if problem:
            return self._reject(session, problem[0], problem[1])

        for role, issue_ids in assignments.items():
            for issue_id in issue_ids:
                session.issues[issue_id].assignee = role
        decision = self._begin_round(
            session,
            RoundKind.DIRECTED,
            assignments={role: tuple(ids) for role, ids in assignments.items()},
        )
        seq = self._append(
            session,
            Role.HOST,
            MessageKind.DECISION,
            reason or "开定向补充轮：" + "，".join(role.value for role in assignments),
        ).seq
        return replace(decision, seq=seq)

    def submit_candidate_draft(self, session: DiscussionSession) -> Decision:
        """主持人判定收敛，派发记录员写候选稿。"""

        if session.status != SessionStatus.DISCUSSING:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不能提交候选稿")
        if session.waiting_for != WaitingFor.HOST_DECISION:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前不在等待主持人调度")
        if session.tasks:
            return self._reject(session, ReasonCode.PENDING_TASKS, "仍有角色任务未提交")
        blockers = draft_blockers(session)
        if blockers:
            return self._reject(
                session,
                ReasonCode.DRAFT_NOT_READY,
                "；".join(code.value for code in blockers),
                detail={
                    "blockers": [code.value for code in blockers],
                    "missing_statements": {
                        topic: [role.value for role in roles]
                        for topic, roles in missing_statements(session).items()
                    },
                    "blocking_issues": list(blocking_issue_ids(session)),
                },
            )
        # 权衡分歧不能靠点名争论收敛：先按维度打分，再由主持人采纳得分最高的方案。
        tradeoffs = self._open_tradeoffs(session)
        if tradeoffs:
            return self._dispatch_scoring(session, tradeoffs)
        self._append(session, Role.HOST, MessageKind.DECISION, "提交候选稿送审")
        return self._enter_phase(session, Phase.DRAFTING)

    # ------------------------------------------------------------ 角色结果
    def submit_output(self, session: DiscussionSession, output: SpeakerOutput) -> Decision:
        """校验并合并一份角色结果。旧输入版本的结果不能推进会话。"""

        if is_terminal(session.status):
            return self._reject(session, ReasonCode.SESSION_TERMINATED, "会话已结束")
        if output.session_id != session.session_id:
            return self._reject(
                session,
                ReasonCode.CROSS_SESSION_RESULT,
                f"结果属于会话 {output.session_id}，不能写入当前会话",
            )
        task = session.tasks.get(output.task_id)
        if task is None:
            if output.task_id in session.stale_tasks:
                return self._reject(
                    session,
                    ReasonCode.STALE_INPUT_VERSION,
                    "输入版本已更新，旧版本结果不能推进当前会话",
                )
            return self._reject(session, ReasonCode.UNKNOWN_TASK, f"未知任务 {output.task_id}")
        if session.status != SessionStatus.DISCUSSING or session.waiting_for != WaitingFor.ROLE_RESULTS:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不接受角色结果")
        if output.role != task.role or output.kind != task.kind:
            return self._reject(
                session, ReasonCode.CONTRACT_MISMATCH, "角色或任务类型与派发不一致"
            )
        if output.round_no != task.round_no or output.topic_id != task.topic_id:
            return self._reject(
                session, ReasonCode.STALE_ROUND, "结果回合或议题与当前派发不一致"
            )
        if (
            output.input_version != session.input_version
            or output.snapshot_ref != session.snapshot_ref
            or task.input_version != session.input_version
        ):
            return self._reject(
                session,
                ReasonCode.STALE_INPUT_VERSION,
                f"结果基于输入版本 {output.input_version}，当前为 {session.input_version}",
            )
        problem = citation_problem(session, output.source_seqs, output.reply_to_seq)
        if problem:
            return self._reject(session, problem[0], problem[1])
        problem = self._validate_payload(session, task, output)
        if problem:
            return self._reject(session, problem[0], problem[1])

        if task.kind == TaskKind.CHALLENGE:
            output = self._remove_repeated_objections(session, output)

        text = f"不适用：{output.not_applicable}" if output.not_applicable else output.text
        seq = self._append(
            session,
            output.role,
            _MESSAGE_KIND[task.kind],
            text,
            reply_to_seq=output.reply_to_seq,
            source_seqs=output.source_seqs,
            item_refs=output.item_refs,
            issue_ids=output.issue_ids
            or tuple(issue.issue_id for issue in output.raised_issues),
            task_id=task.task_id,
            plain_summary=output.plain_summary,
            output=output,
        ).seq
        session.tasks.pop(task.task_id, None)
        session.round_results[task.task_id] = output
        self._bookkeep(session, task, output, seq)

        if task.kind == TaskKind.DRAFT:
            return self._finalize_draft(session, output, seq)
        if task.kind == TaskKind.SCORING:
            return self._finalize_scoring(session, task, output, seq)
        return self._advance_or_wait(session, task.phase)

    # ------------------------------------------------------------ 评审与签收
    @staticmethod
    def _remove_repeated_objections(session: DiscussionSession, output: SpeakerOutput) -> SpeakerOutput:
        def fingerprint(issue: Issue):
            def clean(value):
                return re.sub(r"[\s。！？；.!?;，,：:*]+", "", value or "").casefold()
            return (issue.topic_id, issue.kind, clean(issue.summary), clean(issue.closure_condition))

        earlier: dict[tuple, set[int]] = {}
        for issue in session.issues.values():
            if issue.raised_by == Role.CHALLENGER and issue.input_version == session.input_version:
                earlier.setdefault(fingerprint(issue), set()).update(issue.source_seqs)
        accepted = tuple(issue for issue in output.raised_issues
                         if not set(issue.source_seqs) <= earlier.get(fingerprint(issue), set()))
        repeated = len(output.raised_issues) - len(accepted)
        if not repeated:
            return output
        return replace(output, raised_issues=accepted, text=f"{output.text}\n\n{repeated} 条异议已有记录且没有新证据，未重新派发。")

    def submit_review(self, session: DiscussionSession, review: ReviewResult) -> Decision:
        """评审结论绑定草稿版本与输入版本。"""

        if session.status != SessionStatus.REVIEWING:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不接受评审结论")
        draft = session.current_draft
        if draft is None or review.draft_id != draft.draft_id:
            return self._reject(session, ReasonCode.STALE_DRAFT, "评审针对的草稿不是当前版本")
        if review.session_id != session.session_id:
            return self._reject(session, ReasonCode.CROSS_SESSION_RESULT, "评审不属于当前会话")
        if review.input_version != session.input_version or draft.input_version != session.input_version:
            return self._reject(
                session, ReasonCode.STALE_INPUT_VERSION, "评审针对的输入版本已过期"
            )
        if any(existing.review_id == review.review_id for existing in session.reviews):
            return self._reject(session, ReasonCode.CONTRACT_MISMATCH, "评审 ID 重复")
        for finding in (*review.findings, *review.verified_findings):
            problem = citation_problem(session, finding.source_seqs, None)
            if problem:
                return self._reject(session, *problem)

        pending_verification = {
            issue.issue_id for issue in session.issues.values()
            if issue.input_version == session.input_version and issue.status == IssueStatus.ANSWERED
            and issue.response is not None and issue.severity == Severity.BLOCKING
        }
        verified = {
            finding.issue_id or finding.finding_id for finding in review.verified_findings
            if finding.source_seqs and finding.summary.strip()
        }
        reported = {finding.issue_id or finding.finding_id for finding in review.findings}
        if (pending_verification - (verified | reported)) or (verified & reported):
            return self._reject(
                session, ReasonCode.REVIEW_PASS_WITH_BLOCKING,
                "评审必须逐条复核待验证的阻断问题，明确通过或保留为未解决",
            )

        if review.verdict == Verdict.PASS:
            blocking_findings = [
                finding for finding in review.findings if finding.severity == Severity.BLOCKING
            ]
            if blocking_findings or blocking_issue_ids(session):
                return self._reject(
                    session,
                    ReasonCode.REVIEW_PASS_WITH_BLOCKING,
                    "仍有关键问题未关闭，不能给出通过结论",
                    detail={"blocking_findings": [f.finding_id for f in blocking_findings]},
                )
            self._record_review(session, review)
            draft.review_id = review.review_id
            self._append(
                session,
                Role.REVIEWER,
                MessageKind.REVIEW,
                self._review_text(review, draft),
                plain_summary=review.plain_summary,
                item_refs=tuple(f.target_item for f in review.findings),
            )
            self._transition(session, SessionStatus.AWAITING_CONFIRMATION)
            session.waiting_for = WaitingFor.USER_INPUT
            return self._result(
                session,
                ReasonCode.OK,
                detail={"verdict": "pass", "draft_id": draft.draft_id},
            )

        stalled = review.verdict == Verdict.REVISE and self._revision_stalled(session, draft, review)
        limit_reached = (
            review.verdict == Verdict.REVISE
            and session.max_revision_rounds is not None
            and session.revision_rounds >= session.max_revision_rounds
        )
        if review.verdict == Verdict.NEEDS_INPUT or stalled or limit_reached:
            self._record_review(session, review)
            self._append(
                session,
                Role.REVIEWER,
                MessageKind.REVIEW,
                self._review_text(review, draft),
                plain_summary=review.plain_summary,
                item_refs=tuple(f.target_item for f in review.findings),
            )
            self._transition(session, SessionStatus.CLARIFYING)
            session.waiting_for = WaitingFor.USER_INPUT
            session.round_kind = None
            session.phase = None
            session.round_challenged = False
            questions = review.questions
            if stalled or limit_reached:
                questions = tuple(dict.fromkeys(
                    f"关于「{finding.summary}」，请补充满足以下条件的依据或约束：{finding.close_condition}"
                    for finding in review.findings
                ))
            prompt = "；".join(questions or tuple(f.summary for f in review.findings)) or "需要补充关键事实"
            if limit_reached and not questions:
                prompt = f"修订轮已达上限 {session.max_revision_rounds} 轮，请确认现有取舍或补充依据"
            self._append(
                session,
                Role.REVIEWER,
                MessageKind.CLARIFICATION,
                f"需要用户补充：{prompt}",
                item_refs=tuple(f.target_item for f in review.findings),
                clarification_questions=questions,
            )
            detail: dict[str, object] = {
                "verdict": "needs_input",
                "findings": len(review.findings),
            }
            if limit_reached:
                detail.update(
                    escalated="revision_limit",
                    revision_rounds=session.revision_rounds,
                    max_revision_rounds=session.max_revision_rounds,
                )
            return self._result(session, ReasonCode.AWAITING_USER_INPUT, detail=detail)

        if not review.findings or any(finding.responsible_role not in NAMABLE_ROLES for finding in review.findings):
            return self._reject(
                session,
                ReasonCode.REVISE_WITHOUT_RESPONSIBLE_ROLE,
                "退回修订必须为每个问题指定负责角色",
            )

        self._record_review(session, review)
        draft.review_id = review.review_id
        self._append(
            session,
            Role.REVIEWER,
            MessageKind.REVIEW,
            self._review_text(review, draft),
            plain_summary=review.plain_summary,
            item_refs=tuple(f.target_item for f in review.findings),
        )
        self._transition(session, SessionStatus.AWAITING_CONFIRMATION)
        session.waiting_for = WaitingFor.USER_INPUT
        return self._result(session, ReasonCode.OK, detail={"verdict": "revise", "draft_id": draft.draft_id})

    @staticmethod
    def _record_review(session: DiscussionSession, review: ReviewResult) -> None:
        session.reviews.append(review)
        for finding in review.verified_findings:
            if not finding.source_seqs or not finding.summary.strip():
                continue
            issue = session.issues.get(finding.issue_id or finding.finding_id)
            if issue and issue.input_version == session.input_version and issue.status == IssueStatus.ANSWERED:
                issue.status = IssueStatus.CLOSED
                issue.closed_by_seq = session.next_seq()

    def confirm(
        self, session: DiscussionSession, *, draft_id: str, input_version: int
    ) -> Decision:
        """签收当前评审版本并保留评审原结论。"""

        if session.status != SessionStatus.AWAITING_CONFIRMATION:
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不能签收")
        draft = session.current_draft
        if draft is None or draft.draft_id != draft_id:
            return self._reject(session, ReasonCode.STALE_DRAFT, "签收的草稿不是当前版本")
        if input_version != session.input_version or draft.input_version != input_version:
            return self._reject(
                session, ReasonCode.STALE_INPUT_VERSION, "签收的输入版本不是当前版本"
            )
        review = session.review_of(draft.draft_id)
        if (
            review is None
            or review.verdict not in (Verdict.PASS, Verdict.REVISE)
            or review.input_version != input_version
            or draft.review_id != review.review_id
        ):
            return self._reject(session, ReasonCode.REVIEW_NOT_PASSED, "当前草稿没有同版本的签收评审")
        blockers = draft_blockers(session)
        if blockers:
            return self._reject(
                session,
                ReasonCode.REVIEW_NOT_PASSED,
                "；".join(code.value for code in blockers),
            )

        self._transition(session, SessionStatus.COMPLETED)
        session.waiting_for = WaitingFor.NONE
        text = f"签收草稿 v{draft.version}"
        if review.verdict == Verdict.REVISE:
            text += "（保留建议修订的评审意见）"
        self._append(session, Role.USER, MessageKind.DECISION, text,
                     item_refs=(draft.draft_id, review.review_id))
        return self._result(
            session,
            ReasonCode.OK,
            detail={
                "handoff": {
                    "session_id": session.session_id,
                    "draft_id": draft.draft_id,
                    "draft_version": draft.version,
                    "review_id": review.review_id,
                    "input_version": session.input_version,
                }
            },
        )

    def require_changes(self, session: DiscussionSession, reason: str, *, draft_id: str | None = None) -> Decision:
        """收到用户退回后，按本版评审问题和用户补充意见派发修订。"""
        if session.status not in (SessionStatus.AWAITING_CONFIRMATION, SessionStatus.COMPLETED):
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不能要求修改")
        draft = session.current_draft
        if draft is None or (draft_id is not None and draft_id != draft.draft_id):
            return self._reject(session, ReasonCode.STALE_DRAFT, "请基于当前草稿提交修订意见")
        review = session.review_of(draft.draft_id)
        if review is None or review.verdict not in (Verdict.PASS, Verdict.REVISE):
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前草稿尚未完成评审")
        note = reason.strip() if isinstance(reason, str) else ""
        if not note and review.verdict == Verdict.PASS:
            return self._reject(session, ReasonCode.REASON_REQUIRED, "要求修改需要说明理由")
        item = session.current_item()
        if item is None:
            return self._reject(session, ReasonCode.INVALID_TOPIC, "当前没有可修订的议题")

        assignments = self._revision_assignments(session, review) if review.verdict == Verdict.REVISE else {}
        if assignments is None:
            return self._reject(session, ReasonCode.REVISE_WITHOUT_RESPONSIBLE_ROLE, "评审问题缺少负责角色")
        message = self._append(
            session, Role.USER, MessageKind.DECISION,
            f"退回草稿 v{draft.version}：{note or '按本版评审意见修订'}",
            item_refs=(draft.draft_id,),
        )
        if note:
            role = Role.PRODUCT if Role.PRODUCT in item.role_scope else item.required_roles[0]
            issue_id = f"user-revision-{message.seq}"
            session.issues[issue_id] = Issue(
                issue_id=issue_id, topic_id=item.topic_id, raised_by=Role.USER, summary=note,
                severity=Severity.MAJOR, source_seqs=(message.seq,), assignee=role,
                input_version=session.input_version,
            )
            assignments[role] = (*assignments.get(role, ()), issue_id)
        self._transition(session, SessionStatus.DISCUSSING)
        return self._begin_round(session, RoundKind.REVISION, assignments=assignments)

    # ------------------------------------------------------------ 故障与预算
    def record_failure(
        self, session: DiscussionSession, task_id: str, error: str
    ) -> Decision:
        """记录一次调用失败。重试由角色执行层负责，编排层保持任务待处理。"""

        if task_id not in session.tasks:
            return self._reject(session, ReasonCode.UNKNOWN_TASK, f"未知任务 {task_id}")
        session.failures.setdefault(task_id, []).append(error)
        return self._result(
            session,
            ReasonCode.OK,
            detail={"task_id": task_id, "attempts": len(session.failures[task_id])},
        )

    def abandon_task(
        self, session: DiscussionSession, task_id: str, reason: str
    ) -> Decision:
        """放弃不可恢复的任务，进入暂停等待用户决定。"""

        if task_id not in session.tasks:
            return self._reject(session, ReasonCode.UNKNOWN_TASK, f"未知任务 {task_id}")
        self._pause(session, reason or "角色任务失败，讨论暂停")
        return self._result(session, ReasonCode.OK, detail={"task_id": task_id})

    def pause(self, session: DiscussionSession, reason: str = "") -> Decision:
        if session.status == SessionStatus.PAUSED:
            return self._reject(session, ReasonCode.INVALID_STATUS, "会话已暂停")
        if not can_pause(session.status):
            return self._reject(session, ReasonCode.INVALID_STATUS, "当前阶段不能暂停")
        self._pause(session, reason or "讨论已暂停")
        return self._result(session, ReasonCode.OK)

    def resume(self, session: DiscussionSession) -> Decision:
        if session.status != SessionStatus.PAUSED:
            return self._reject(session, ReasonCode.INVALID_STATUS, "会话未暂停")
        if not can_resume(session.paused_from):
            return self._reject(session, ReasonCode.INVALID_TRANSITION, "缺少可恢复的暂停点")
        legacy_dispatch_pause = next(
            (message.text for message in reversed(session.messages) if message.kind == MessageKind.SYSTEM),
            "",
        ) == "讨论调用预算耗尽，等待用户授权追加"
        session.status = session.paused_from
        session.paused_from = None
        fallback = {
            SessionStatus.CLARIFYING: WaitingFor.HOST_AGENDA,
            SessionStatus.REVIEWING: WaitingFor.REVIEW,
            SessionStatus.AWAITING_CONFIRMATION: WaitingFor.USER_INPUT,
        }
        session.waiting_for = session.paused_waiting_for or fallback.get(
            session.status, WaitingFor.ROLE_RESULTS if session.tasks else WaitingFor.HOST_DECISION)
        session.paused_waiting_for = None
        self._append(session, Role.HOST, MessageKind.SYSTEM, "已恢复讨论")
        if legacy_dispatch_pause and session.status == SessionStatus.DISCUSSING and not session.tasks and session.phase:
            return self._enter_phase(session, session.phase)
        return self._result(session, ReasonCode.OK)

    def cancel(self, session: DiscussionSession, reason: str = "") -> Decision:
        if is_terminal(session.status):
            return self._reject(session, ReasonCode.SESSION_TERMINATED, "会话已结束")
        session.status = SessionStatus.CANCELED
        session.paused_from = None
        session.paused_waiting_for = None
        session.tasks.clear()
        session.waiting_for = WaitingFor.NONE
        self._append(
            session,
            Role.USER,
            MessageKind.DECISION,
            f"取消讨论：{reason}" if reason else "取消讨论",
        )
        return self._result(session, ReasonCode.OK)

    # ------------------------------------------------------------ 内部实现
    def _begin_round(
        self,
        session: DiscussionSession,
        kind: RoundKind,
        *,
        assignments: Mapping[Role, tuple[str, ...]] | None = None,
    ) -> Decision:
        session.round_no += 1
        session.round_kind = kind
        session.phase = _FIRST_PHASE[kind]
        session.round_results.clear()
        session.round_challenged = False
        session.named_responses = {
            role: tuple(issue_ids) for role, issue_ids in (assignments or {}).items()
        }
        if kind == RoundKind.DIRECTED:
            session.directed_rounds += 1
        elif kind == RoundKind.REVISION:
            session.revision_rounds += 1
        return self._enter_phase(session, session.phase)

    def _enter_phase(self, session: DiscussionSession, phase: Phase) -> Decision:
        session.phase = phase
        dispatch = self._dispatch_phase(session, phase)
        if dispatch.code != ReasonCode.OK:
            return self._result(
                session,
                dispatch.code,
                ok=False,
                message="当前没有可派发的议题",
            )
        session.waiting_for = WaitingFor.ROLE_RESULTS
        return self._result(session, ReasonCode.OK, tasks=dispatch.tasks)

    def _dispatch_phase(self, session: DiscussionSession, phase: Phase) -> _Dispatch:
        item = session.current_item()
        if item is None:
            return _Dispatch(code=ReasonCode.INVALID_TOPIC)
        seqs = self._context_seqs(session)
        specs: list[tuple[Role, TaskKind, tuple[str, ...]]] = []
        if phase == Phase.STATEMENTS:
            for role in item.required_roles:
                specs.append((role, TaskKind.STATEMENT, ()))
        elif phase == Phase.CHALLENGE:
            session.round_challenged = True
            specs.append((Role.CHALLENGER, TaskKind.CHALLENGE, ()))
        elif phase == Phase.RESPONSES:
            for role, issue_ids in session.named_responses.items():
                specs.append((role, TaskKind.RESPONSE, tuple(issue_ids)))
        elif phase == Phase.INTEGRATION:
            specs.append((Role.RECORDER, TaskKind.INTEGRATION, ()))
        elif phase == Phase.DRAFTING:
            specs.append((Role.RECORDER, TaskKind.DRAFT, ()))
        tasks: list[SpeakerTask] = []
        for ordinal, (role, kind, issue_ids) in enumerate(specs):
            task_id = (
                f"{session.session_id}:r{session.round_no}:{phase.value}:"
                f"{role.value}:{session.dispatch_units + ordinal}"
            )
            tasks.append(
                SpeakerTask(
                    task_id=task_id,
                    session_id=session.session_id,
                    role=role,
                    kind=kind,
                    phase=phase,
                    round_kind=session.round_kind or RoundKind.FIRST,
                    round_no=session.round_no,
                    topic_id=item.topic_id,
                    snapshot_ref=session.snapshot_ref,
                    input_version=session.input_version,
                    message_seqs=seqs,
                    issue_ids=issue_ids,
                )
            )
        for task in tasks:
            session.tasks[task.task_id] = task
            session.dispatch_units += 1
        return _Dispatch(tasks=tuple(tasks))

    def _context_seqs(self, session: DiscussionSession) -> tuple[int, ...]:
        seqs = {message.seq for message in session.messages if message.round_no == session.round_no}
        seqs |= {
            message.seq
            for message in session.messages
            if message.role == Role.USER and message.input_version == session.input_version
        }
        seqs |= {
            message.seq
            for message in session.messages
            if message.kind == MessageKind.AGENDA
            and message.input_version == session.input_version
        }
        return tuple(sorted(seqs))

    def _advance_or_wait(self, session: DiscussionSession, phase: Phase) -> Decision:
        pending = [task for task in session.tasks.values() if task.phase == phase]
        if pending:
            return self._result(session, ReasonCode.OK)
        return self._advance(session, phase)

    def _advance(self, session: DiscussionSession, phase: Phase) -> Decision:
        if phase == Phase.STANCES:
            return self._complete_scoring(session)
        if phase == Phase.STATEMENTS:
            return self._enter_phase(session, Phase.CHALLENGE)
        if phase == Phase.RESPONSES:
            if not session.round_challenged:
                return self._enter_phase(session, Phase.CHALLENGE)
            if session.open_issues(session.topic_id):
                return self._await_host(session)
            return self._enter_phase(session, Phase.INTEGRATION)
        if phase == Phase.CHALLENGE:
            if session.open_issues(session.topic_id):
                return self._await_host(session)
            return self._enter_phase(session, Phase.INTEGRATION)
        return self._await_host(session)

    def _await_host(self, session: DiscussionSession) -> Decision:
        session.waiting_for = WaitingFor.HOST_DECISION
        return self._result(
            session,
            ReasonCode.AWAITING_HOST_DECISION,
            detail={
                "round_no": session.round_no,
                "round_kind": session.round_kind.value if session.round_kind else None,
                "open_issue_ids": [
                    issue.issue_id for issue in session.open_issues(session.topic_id)
                ],
                "draft_blockers": [code.value for code in draft_blockers(session)],
                "directed_rounds": session.directed_rounds,
                "revision_rounds": session.revision_rounds,
                "max_directed_rounds": session.max_directed_rounds,
                "max_revision_rounds": session.max_revision_rounds,
                "remaining_directed_rounds": (
                    max(0, session.max_directed_rounds - session.directed_rounds)
                    if session.max_directed_rounds is not None else None
                ),
                "remaining_revision_rounds": (
                    max(0, session.max_revision_rounds - session.revision_rounds)
                    if session.max_revision_rounds is not None else None
                ),
                "tradeoff_issue_ids": [
                    issue.issue_id for issue in self._open_tradeoffs(session)
                ],
                "scoring_required": bool(self._open_tradeoffs(session)),
                "chosen_option_id": session.chosen_option_id,
            },
        )

    def _validate_payload(
        self, session: DiscussionSession, task: SpeakerTask, output: SpeakerOutput
    ) -> tuple[ReasonCode, str] | None:
        if task.kind == TaskKind.STATEMENT:
            if output.not_applicable is not None:
                if task.role != Role.BUSINESS:
                    return (
                        ReasonCode.NOT_APPLICABLE_NOT_ALLOWED,
                        "只有商业/市场分析可以提交有理由的「不适用」",
                    )
                if not output.not_applicable.strip():
                    return (ReasonCode.REASON_REQUIRED, "「不适用」必须给出理由")
            elif not output.text.strip():
                return (ReasonCode.EMPTY_OUTPUT, "发言正文不能为空")
            if output.raised_issues:
                return (ReasonCode.CONTRACT_MISMATCH, "只有质疑者可以提交质疑条目")
            return None

        if task.kind == TaskKind.CHALLENGE:
            if not output.text.strip():
                return (ReasonCode.EMPTY_OUTPUT, "质疑发言正文不能为空")
            if not output.raised_issues:
                return (
                    ReasonCode.CHALLENGE_NEEDS_ISSUE,
                    "质疑者至少提出一条指向具体发言或用户输入的问题",
                )
            item = session.current_item()
            new_ids: set[str] = set()
            known = known_seqs(session)
            for issue in output.raised_issues:
                if not issue.issue_id.strip():
                    return (ReasonCode.INVALID_ISSUE, "问题 ID 不能为空")
                if issue.issue_id in session.issues or issue.issue_id in new_ids:
                    return (ReasonCode.INVALID_ISSUE, f"问题 {issue.issue_id} 已存在")
                new_ids.add(issue.issue_id)
                if issue.assignee is not None and (not item or issue.assignee not in item.role_scope):
                    return (ReasonCode.INVALID_ISSUE, "质疑点名角色不在当前议题范围内")
                if issue.topic_id != task.topic_id:
                    return (ReasonCode.INVALID_ISSUE, "问题所属议题与当前议题不一致")
                if not issue.summary.strip():
                    return (ReasonCode.INVALID_ISSUE, "问题描述不能为空")
                if issue.kind == DivergenceKind.ROUTINE and (issue.severity == Severity.BLOCKING or issue.needs_user
                        or issue.user_question or not (issue.proposed_resolution or "").strip() or not (issue.reopen_if or "").strip()):
                    return (ReasonCode.INVALID_ISSUE, "可逆细节必须有默认做法和重开条件，且不能是阻断问题")
                if not issue.source_seqs:
                    return (
                        ReasonCode.CHALLENGE_NEEDS_REFERENCE,
                        f"问题 {issue.issue_id} 必须引用具体发言或用户输入",
                    )
                for seq in issue.source_seqs:
                    if seq not in known:
                        return (
                            ReasonCode.CROSS_SESSION_REFERENCE,
                            f"问题 {issue.issue_id} 引用了不属于当前会话的消息 {seq}",
                        )
            return None

        if task.kind == TaskKind.RESPONSE:
            if not output.text.strip():
                return (ReasonCode.EMPTY_OUTPUT, "回应正文不能为空")
            responses = {response.issue_id: response for response in output.responses}
            if len(responses) != len(output.responses) or (
                responses and (set(responses) != set(output.issue_ids) or set(responses) != set(task.issue_ids))
            ):
                return (ReasonCode.INVALID_ISSUE, "每个指派问题都必须有唯一的逐条回应")
            if len(output.issue_ids) > 1 and not responses:
                return (ReasonCode.INVALID_ISSUE, "多个问题必须分别提交处理方式和验证方法")
            if not output.issue_ids:
                return (ReasonCode.INVALID_ISSUE, "回应必须针对被点名的问题")
            for issue_id in output.issue_ids:
                if issue_id not in task.issue_ids:
                    return (
                        ReasonCode.INVALID_ISSUE,
                        f"问题 {issue_id} 未被指派给 {task.role.value}",
                    )
                issue = session.issues.get(issue_id)
                if issue is None:
                    return (ReasonCode.UNKNOWN_ISSUE, f"问题 {issue_id} 不存在")
                if issue.needs_user:
                    return (ReasonCode.ISSUE_NEEDS_USER, f"问题 {issue_id} 等待用户补充事实")
                if issue.status != IssueStatus.OPEN:
                    return (ReasonCode.ISSUE_ALREADY_CLOSED, f"问题 {issue_id} 已处理")
                response = responses.get(issue_id)
                if response:
                    if not response.treatment.strip() or not response.verification.strip():
                        return (ReasonCode.MISSING_EVIDENCE, "逐条回应必须包含处理方式和验证方法")
                    problem = citation_problem(session, response.source_seqs, None)
                    if problem:
                        return problem
                if issue.severity == Severity.BLOCKING and not (response or output.evidence):
                    return (
                        ReasonCode.MISSING_EVIDENCE,
                        f"阻断问题 {issue_id} 关闭前必须给出证据或可验证的处理方式",
                    )
            return None

        if task.kind == TaskKind.STANCE:
            card = self._latest_scorecard(session)
            option_ids = [stance.option_id for stance in output.stances]
            if (not card or len(option_ids) != len(set(option_ids))
                    or set(option_ids) != {option.option_id for option in card.options}):
                return (ReasonCode.INVALID_SCORECARD, "必须逐项提交本次备选方案的立场")
            for stance in output.stances:
                if stance.position not in {"support", "oppose", "conditional"} or not stance.reason.strip():
                    return (ReasonCode.INVALID_SCORECARD, "每项立场必须包含明确的选择及理由")
                problem = citation_problem(session, stance.source_seqs, None)
                if problem:
                    return problem
                if stance.score_dimension is not None and (
                    not isinstance(stance.score_dimension, ScoreDimension) or stance.position == "support" or not stance.source_seqs
                ):
                    return (ReasonCode.INVALID_SCORECARD, "评分调整需要有引用依据的反对或有条件赞同")
            return None

        if task.kind == TaskKind.INTEGRATION:
            if not output.text.strip():
                return (ReasonCode.EMPTY_OUTPUT, "整合稿不能为空")
            if not output.source_seqs:
                return (
                    ReasonCode.CROSS_SESSION_REFERENCE,
                    "整合稿必须引用本轮已提交发言",
                )
            return None

        if task.kind == TaskKind.DRAFT:
            if not output.draft_content:
                return (ReasonCode.DRAFT_CONTENT_MISSING, "候选稿缺少正文内容")
            blockers = draft_blockers(session)
            if blockers:
                return (
                    ReasonCode.DRAFT_NOT_READY,
                    "；".join(code.value for code in blockers),
                )
            return None

        if task.kind == TaskKind.SCORING:
            if output.scorecard is None:
                return (ReasonCode.INVALID_SCORECARD, "打分结果缺少评分卡")
            problem = scorecard_problem(output.scorecard.options, output.scorecard.weights)
            if problem:
                return problem
            if output.scorecard.topic_id and output.scorecard.topic_id != session.topic_id:
                return (ReasonCode.INVALID_SCORECARD, "评分卡议题与当前议题不一致")
            return None
        return None

    def _bookkeep(
        self,
        session: DiscussionSession,
        task: SpeakerTask,
        output: SpeakerOutput,
        seq: int,
    ) -> None:
        if task.kind == TaskKind.STATEMENT:
            session.covered_statements.setdefault(task.topic_id, set()).add(task.role)
        elif task.kind == TaskKind.CHALLENGE:
            session.challenged_topics.add(task.topic_id)
            for issue in output.raised_issues:
                session.issues[issue.issue_id] = replace(
                    issue,
                    assignee=issue.assignee if issue.kind == DivergenceKind.FACT else None,
                    source_seqs=tuple(dict.fromkeys((*issue.source_seqs, seq))),
                    # 价值分歧不能靠角色互相说服，直接标记为待用户裁决。
                    needs_user=issue.kind == DivergenceKind.VALUE,
                    status=IssueStatus.OPEN,
                    closed_by_seq=None,
                    input_version=session.input_version,
                )
        elif task.kind == TaskKind.STANCE:
            card = self._latest_scorecard(session)
            if card is not None:
                index = session.scorecards.index(card)
                session.scorecards[index] = replace(card, stances={**card.stances, task.role: output.stances})
        elif task.kind == TaskKind.RESPONSE:
            responses = {response.issue_id: response for response in output.responses}
            for issue_id in output.issue_ids:
                issue = session.issues.get(issue_id)
                if issue is not None and issue.status == IssueStatus.OPEN:
                    issue.status = IssueStatus.ANSWERED
                    issue.response = responses.get(issue_id)
                    if issue.response is None and issue.severity == Severity.BLOCKING:
                        issue.response = IssueResponse(issue_id, output.text, "；".join(output.evidence), output.source_seqs)
                    issue.response_seq = seq
                    issue.closed_by_seq = None if issue.response else seq
                    issue.assignee = task.role

    @staticmethod
    def _open_tradeoffs(session: DiscussionSession) -> tuple[Issue, ...]:
        return tuple(
            issue
            for issue in session.open_issues(session.topic_id)
            if issue.kind == DivergenceKind.TRADEOFF
        )

    @staticmethod
    def _latest_scorecard(session: DiscussionSession) -> Scorecard | None:
        for card in reversed(session.scorecards):
            if card.input_version == session.input_version and card.topic_id == (session.topic_id or ""):
                return card
        return None

    def _dispatch_scoring(
        self, session: DiscussionSession, issues: tuple[Issue, ...]
    ) -> Decision:
        item = session.current_item()
        if item is None:
            return self._reject(session, ReasonCode.INVALID_TOPIC, "当前没有可打分的议题")
        task = SpeakerTask(
            task_id=f"{session.session_id}:score:{session.dispatch_units}",
            session_id=session.session_id,
            role=Role.HOST,
            kind=TaskKind.SCORING,
            phase=Phase.INTEGRATION,
            round_kind=session.round_kind or RoundKind.FIRST,
            round_no=session.round_no,
            topic_id=item.topic_id,
            snapshot_ref=session.snapshot_ref,
            input_version=session.input_version,
            message_seqs=self._context_seqs(session),
            issue_ids=tuple(issue.issue_id for issue in issues),
        )
        session.tasks[task.task_id] = task
        session.dispatch_units += 1
        session.waiting_for = WaitingFor.ROLE_RESULTS
        return self._result(
            session,
            ReasonCode.OK,
            tasks=(task,),
            detail={
                "scoring_required": True,
                "tradeoff_issue_ids": [issue.issue_id for issue in issues],
                "topic_id": item.topic_id,
            },
        )

    def _finalize_scoring(
        self, session: DiscussionSession, task: SpeakerTask, output: SpeakerOutput, seq: int
    ) -> Decision:
        card = output.scorecard
        assert card is not None  # _validate_payload 已拦截
        stored = replace(
            card,
            scorecard_id=f"{session.session_id}:score:{len(session.scorecards) + 1}",
            session_id=session.session_id,
            topic_id=session.topic_id or "",
            input_version=session.input_version,
            weights=normalize_weights(card.weights) or {},
            chosen_option_id="",
            stances={},
        )
        session.scorecards.append(stored)
        item = session.current_item()
        message_seqs = tuple(number for number in self._context_seqs(session) if number < seq)
        stance_tasks = tuple(
            SpeakerTask(
                task_id=f"{stored.scorecard_id}:stance:{role.value}",
                session_id=session.session_id, role=role, kind=TaskKind.STANCE, phase=Phase.STANCES,
                round_kind=session.round_kind or RoundKind.FIRST, round_no=session.round_no,
                topic_id=stored.topic_id, snapshot_ref=session.snapshot_ref,
                input_version=session.input_version, message_seqs=message_seqs,
                issue_ids=task.issue_ids,
            )
            for role in (item.required_roles if item else ())
        )
        for task in stance_tasks:
            session.tasks[task.task_id] = task
        session.dispatch_units += len(stance_tasks)
        if not stance_tasks:
            return self._complete_scoring(session)
        session.phase = Phase.STANCES
        session.waiting_for = WaitingFor.ROLE_RESULTS
        return self._result(session, ReasonCode.OK, tasks=stance_tasks, detail={"scorecard_id": stored.scorecard_id})

    def _complete_scoring(self, session: DiscussionSession) -> Decision:
        card = self._latest_scorecard(session)
        if card is None:
            return self._reject(session, ReasonCode.INVALID_SCORECARD, "当前议题缺少评分卡")
        objections: dict[str, dict[ScoreDimension, set[int]]] = {}
        for stances in card.stances.values():
            for stance in stances:
                if stance.score_dimension is not None:
                    dimensions = objections.setdefault(stance.option_id, {})
                    dimensions.setdefault(stance.score_dimension, set()).update(stance.source_seqs)
        labels = {ScoreDimension.USER_VALUE: "用户价值", ScoreDimension.FEASIBILITY: "可行性",
                  ScoreDimension.COST: "成本可控性", ScoreDimension.DIFFERENTIATION: "差异化", ScoreDimension.RISK: "风险可控性"}
        options, changes, sources = [], [], set()
        for option in card.options:
            scores = dict(option.scores)
            for dimension in ScoreDimension:
                refs = objections.get(option.option_id, {}).get(dimension)
                if refs and scores[dimension] > 0:
                    scores[dimension] -= 1
                    changes.append(f"{option.option_id} {labels[dimension]} {option.scores[dimension]}→{scores[dimension]}")
                    sources.update(refs)
            options.append(replace(option, scores=scores))
        rationale = card.rationale
        if changes:
            rationale = " ".join(filter(None, (rationale, f"根据有依据的角色异议复核评分：{'、'.join(changes)}。")))
        ranked = rank_options(tuple(options), card.weights)
        stored = replace(card, options=tuple(options), rationale=rationale, chosen_option_id=ranked[0].option_id)
        session.scorecards[session.scorecards.index(card)] = stored
        session.chosen_option_id = stored.chosen_option_id
        ranking = "；".join(f"{item.rank}. {item.option_id}「{item.title}」{item.total:.2f}" for item in ranked)
        text = f"收敛评分：{ranking}。采纳 {stored.chosen_option_id}。"
        if stored.rationale:
            text += f"依据：{stored.rationale}"
        decision = self._append(session, Role.HOST, MessageKind.DECISION, text,
                                plain_summary=f"本轮暂选「{ranked[0].title}」。",
                                item_refs=tuple(item.option_id for item in ranked), source_seqs=tuple(sorted(sources)))
        for issue in self._open_tradeoffs(session):
            issue.status = IssueStatus.CLOSED
            issue.closed_by_seq = decision.seq
            issue.assignee = Role.HOST
        session.waiting_for = WaitingFor.HOST_DECISION
        return self._result(
            session,
            ReasonCode.OK,
            detail={
                "scorecard_id": stored.scorecard_id,
                "chosen_option_id": stored.chosen_option_id,
                "weights": {dimension.value: weight for dimension, weight in stored.weights.items()},
                "ranking": [
                    {
                        "option_id": item.option_id,
                        "title": item.title,
                        "total": item.total,
                        "rank": item.rank,
                        "scores": {dimension.value: value for dimension, value in item.scores.items()},
                    }
                    for item in ranked
                ],
            },
        )

    def _finalize_draft(
        self, session: DiscussionSession, output: SpeakerOutput, seq: int
    ) -> Decision:
        # 用户补充会取消旧稿的 current 标记，但上一份冻结稿仍是新版本的父稿。
        current = session.drafts[-1] if session.drafts else None
        for draft in session.drafts:
            draft.current = False
        version = len(session.drafts) + 1
        scorecard = self._latest_scorecard(session)
        draft = DraftRevision(
            draft_id=f"{session.session_id}-v{version}",
            version=version,
            input_version=session.input_version,
            content=dict(output.draft_content or {}),
            source_seqs=output.source_seqs,
            parent_id=current.draft_id if current else None,
            open_issue_ids=tuple(issue.issue_id for issue in session.open_issues()),
            agreements=tuple(output.agreements),
            alternatives=tuple(output.alternatives),
            disagreements=tuple(output.disagreements),
            assumptions=tuple(output.assumptions),
            risks=tuple(output.risks),
            scorecard_id=scorecard.scorecard_id if scorecard else None,
            chosen_option_id=scorecard.chosen_option_id if scorecard else None,
        )
        session.drafts.append(draft)
        self._transition(session, SessionStatus.REVIEWING)
        message = self._append(
            session,
            Role.RECORDER,
            MessageKind.DRAFT,
            f"候选稿 v{version} 已冻结送审",
            source_seqs=output.source_seqs,
            item_refs=output.item_refs,
        )
        session.waiting_for = WaitingFor.REVIEW
        return self._result(
            session,
            ReasonCode.AWAITING_REVIEW,
            seq=message.seq,
            detail={
                "draft_id": draft.draft_id,
                "version": version,
                "parent_id": draft.parent_id,
                "frozen_at_seq": seq,
            },
        )

    @staticmethod
    def _revision_stalled(session: DiscussionSession, draft: DraftRevision, review: ReviewResult) -> bool:
        if not review.findings:
            return False

        def scope(result: ReviewResult) -> set[tuple[str, str]]:
            return {(finding.issue_id or finding.finding_id, finding.target_item) for finding in result.findings}

        def content(revision: DraftRevision) -> dict:
            body = dict(revision.content)
            body.pop("removed_requirement_ids", None)
            if isinstance(body.get("requirements"), (list, tuple)):
                body["requirements"] = [
                    {key: value for key, value in item.items() if key != "source_seqs"}
                    if isinstance(item, Mapping) else item
                    for item in body["requirements"]
                ]
            return body

        current_scope = scope(review)
        current_content = content(draft)
        prior_reviews = {
            item.draft_id: item for item in session.reviews
            if item.input_version == draft.input_version and item.verdict == Verdict.REVISE
        }
        previous = [prior for prior in session.drafts if prior.draft_id in prior_reviews]
        if any(scope(prior_reviews[prior.draft_id]) == current_scope
               and content(prior) == current_content for prior in previous):
            return True

        # Look for progress on the affected parts, rather than rewarding edits
        # elsewhere in the draft. Compare two review rounds to detect A/B churn.
        targets = {finding.target_item for finding in review.findings}

        def normalize(value):
            if isinstance(value, str):
                return re.sub(r"\s+", "", value).rstrip("。！？；.;!?")
            if isinstance(value, Mapping):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [normalize(item) for item in value]
            return value

        def substance(revision):
            body = content(revision)
            requirements = {
                item.get("id"): item for item in body.get("requirements", ()) if isinstance(item, Mapping)
            }
            if all(target in current_content or target in current_requirements for target in targets):
                return normalize({
                    target: body.get(target, requirements.get(target)) for target in targets
                })
            return normalize({
                key: value for key, value in body.items()
                if key not in {"product_name", "background", "supplemental_text"} - targets
            })

        def affected(result):
            return {(finding.target_item, finding.severity) for finding in result.findings}

        current_requirements = {
            item.get("id") for item in current_content.get("requirements", ()) if isinstance(item, Mapping)
        }
        recent = [
            prior for prior in session.drafts
            if prior.input_version == draft.input_version and prior.version < draft.version
        ][-2:]
        if len(recent) != 2 or any(
            prior.draft_id not in prior_reviews or affected(prior_reviews[prior.draft_id]) != affected(review)
            for prior in recent
        ):
            return False
        current_substance = substance(draft)
        return any(substance(prior) == current_substance for prior in recent)

    def _revision_assignments(
        self, session: DiscussionSession, review: ReviewResult
    ) -> dict[Role, tuple[str, ...]] | None:
        responsible = [
            finding
            for finding in review.findings
            if finding.responsible_role is not None
            and finding.responsible_role in NAMABLE_ROLES
        ]
        if not responsible or len(responsible) != len(review.findings):
            return None
        topic_id = session.topic_id
        if topic_id is None and session.agenda is not None:
            topic_id = session.agenda.items[0].topic_id
        grouped: dict[Role, list[str]] = {}
        for index, finding in enumerate(review.findings, start=1):
            role = finding.responsible_role
            if role is None or role not in NAMABLE_ROLES:
                continue
            issue_id = finding.issue_id or f"{review.review_id}-F{index}"
            existing = session.issues.get(issue_id)
            if existing is None:
                session.issues[issue_id] = Issue(
                    issue_id=issue_id,
                    topic_id=topic_id or "",
                    raised_by=Role.REVIEWER,
                    summary=finding.summary,
                    severity=finding.severity,
                    source_seqs=finding.source_seqs,
                    assignee=role,
                    input_version=session.input_version,
                )
            else:
                existing.summary = finding.summary
                existing.severity = finding.severity
                existing.source_seqs = finding.source_seqs
                existing.status = IssueStatus.OPEN
                existing.assignee = role
                existing.needs_user = False
                existing.closed_by_seq = None
                existing.response = None
                existing.response_seq = None
                existing.input_version = session.input_version
            grouped.setdefault(role, []).append(issue_id)
        return {role: tuple(ids) for role, ids in grouped.items()}

    @staticmethod
    def _review_text(review: ReviewResult, draft: DraftRevision) -> str:
        verdict = {
            Verdict.PASS: "通过",
            Verdict.REVISE: "退回修订",
            Verdict.NEEDS_INPUT: "需要用户补充",
        }[review.verdict]
        details = "；".join(
            f"{finding.finding_id}/{finding.severity.value}：{finding.summary}"
            for finding in review.findings
        )
        return f"评审 {verdict} 草稿 v{draft.version}" + (f"。{details}" if details else "")

    def _pause(self, session: DiscussionSession, message: str) -> None:
        if session.status == SessionStatus.PAUSED:
            return
        if not can_pause(session.status):
            return
        session.paused_from = session.status
        session.paused_waiting_for = session.waiting_for
        session.status = SessionStatus.PAUSED
        session.waiting_for = WaitingFor.HOST_DECISION
        self._append(session, Role.HOST, MessageKind.SYSTEM, message)

    def _transition(self, session: DiscussionSession, target: SessionStatus) -> None:
        assert_transition(session.status, target)
        session.status = target

    def _append(
        self,
        session: DiscussionSession,
        role: Role,
        kind: MessageKind,
        text: str,
        *,
        topic_id: str | None = None,
        reply_to_seq: int | None = None,
        source_seqs: tuple[int, ...] = (),
        item_refs: tuple[str, ...] = (),
        issue_ids: tuple[str, ...] = (),
        task_id: str | None = None,
        clarification_questions: tuple[str, ...] = (),
        clarification_answers: tuple[ClarificationAnswer, ...] = (),
        replaces_answer_seq: int | None = None,
        substantive: bool = True,
        plain_summary: str | None = None,
        output: SpeakerOutput | None = None,
    ) -> Message:
        message = Message(
            seq=session.next_seq(),
            role=role,
            kind=kind,
            text=text,
            input_version=session.input_version,
            round_no=session.round_no,
            topic_id=session.topic_id if topic_id is None else topic_id,
            reply_to_seq=reply_to_seq,
            source_seqs=tuple(source_seqs),
            item_refs=tuple(item_refs),
            issue_ids=tuple(issue_ids),
            task_id=task_id,
            clarification_questions=clarification_questions,
            clarification_answers=clarification_answers,
            replaces_answer_seq=replaces_answer_seq,
            substantive=substantive,
            plain_summary=plain_summary,
            **({key: getattr(output, key) for key in (
                "basis", "verification", "agreements", "alternatives", "disagreements", "assumptions", "risks",
            )} if output is not None else {}),
        )
        session.messages.append(message)
        return message

    def _activate_topic(self, session: DiscussionSession, item) -> None:
        session.topic_id = item.topic_id
        session.named_responses.clear()
        session.round_results.clear()
        session.round_challenged = False

    def _result(
        self,
        session: DiscussionSession,
        code: ReasonCode,
        *,
        ok: bool = True,
        message: str = "",
        tasks: tuple[SpeakerTask, ...] = (),
        seq: int | None = None,
        detail: Mapping[str, object] | None = None,
    ) -> Decision:
        return Decision(
            ok=ok,
            code=code,
            message=message,
            tasks=tuple(tasks),
            seq=session.last_seq if seq is None else seq,
            status=session.status,
            waiting_for=session.waiting_for,
            detail=dict(detail or {}),
        )

    def _reject(
        self,
        session: DiscussionSession,
        code: ReasonCode,
        message: str,
        *,
        detail: Mapping[str, object] | None = None,
    ) -> Decision:
        session.rejections.append(
            Rejection(
                task_id="",
                code=code,
                note=message,
                input_version=session.input_version,
                round_no=session.round_no,
            )
        )
        return self._result(session, code, ok=False, message=message, detail=detail)


_MESSAGE_KIND: dict[TaskKind, MessageKind] = {
    TaskKind.STATEMENT: MessageKind.STATEMENT,
    TaskKind.CHALLENGE: MessageKind.CHALLENGE,
    TaskKind.RESPONSE: MessageKind.RESPONSE,
    TaskKind.STANCE: MessageKind.STANCE,
    TaskKind.INTEGRATION: MessageKind.INTEGRATION,
    TaskKind.DRAFT: MessageKind.DRAFT,
    TaskKind.SCORING: MessageKind.DECISION,
}
