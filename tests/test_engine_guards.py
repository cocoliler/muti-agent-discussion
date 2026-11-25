"""编排防线测试：轮次硬上限、价值分歧转人工、分歧记录随草稿留存。

这些是把设计稿里「无限循环」「假共识」「分歧记录」三条落成代码后的回归。
"""

from __future__ import annotations

import unittest

from discussion_orchestration import (
    Agenda,
    AgendaItem,
    DivergenceKind,
    DiscussionEngine,
    DraftRevision,
    Issue,
    Phase,
    ReasonCode,
    ReviewFinding,
    ReviewResult,
    Role,
    RoundKind,
    SessionStatus,
    Severity,
    SpeakerOutput,
    SpeakerTask,
    TaskKind,
    Verdict,
    WaitingFor,
    draft_blockers,
)
from discussion_orchestration.validation import validate_assignments

IDEA = "名称：会议室预约。用户：行政人员。目标：预约会议室。"


def build_session(engine: DiscussionEngine):
    session = engine.create_session(session_id="s-guard", project_id="p-guard", idea=IDEA)
    agenda = Agenda(
        summary="明确首版范围",
        items=(AgendaItem(topic_id="T1", title="预约与冲突", question="如何避免重复预约？"),),
    )
    decision = engine.submit_agenda(session, agenda)
    assert decision.ok, decision.code
    return session


class RoundLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = DiscussionEngine()

    def test_directed_round_stops_at_limit(self) -> None:
        session = build_session(self.engine)
        session.tasks.clear()
        session.waiting_for = WaitingFor.HOST_DECISION
        session.directed_rounds = session.max_directed_rounds

        decision = self.engine.start_directed_round(
            session, {Role.PRODUCT: ("I-1",)}, "继续补充"
        )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.code, ReasonCode.ROUND_LIMIT)
        self.assertEqual(decision.detail["max_directed_rounds"], session.max_directed_rounds)

    def test_revision_limit_escalates_to_user(self) -> None:
        session = build_session(self.engine)
        session.tasks.clear()
        session.status = SessionStatus.REVIEWING
        session.waiting_for = WaitingFor.REVIEW
        session.revision_rounds = session.max_revision_rounds
        draft = DraftRevision(
            draft_id="s-guard-v1",
            version=1,
            input_version=session.input_version,
            content={"requirements": [{"id": "FR-01"}]},
            source_seqs=(1,),
        )
        session.drafts.append(draft)
        review = ReviewResult(
            review_id="r-1",
            session_id=session.session_id,
            draft_id=draft.draft_id,
            input_version=session.input_version,
            verdict=Verdict.REVISE,
            findings=(
                ReviewFinding(
                    finding_id="F-1",
                    severity=Severity.BLOCKING,
                    target_item="FR-01",
                    summary="验收条件不可观察",
                    close_condition="补充可执行的验收条件",
                    responsible_role=Role.PRODUCT,
                    source_seqs=(1,),
                ),
            ),
        )

        decision = self.engine.submit_review(session, review)

        self.assertTrue(decision.ok, decision.code)
        self.assertEqual(decision.code, ReasonCode.AWAITING_USER_INPUT)
        self.assertEqual(session.status, SessionStatus.CLARIFYING)
        self.assertEqual(decision.detail["escalated"], "revision_limit")
        self.assertTrue(session.messages[-1].clarification_questions)


class ValueDivergenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = DiscussionEngine()

    def test_value_issue_blocks_draft_and_cannot_be_named(self) -> None:
        session = build_session(self.engine)
        item = session.current_item()
        session.covered_statements = {item.topic_id: set(item.required_roles)}
        session.challenged_topics = {item.topic_id}
        session.issues["I-V"] = Issue(
            issue_id="I-V",
            topic_id=item.topic_id,
            raised_by=Role.CHALLENGER,
            summary="是否优先支持匿名预约",
            severity=Severity.MAJOR,
            kind=DivergenceKind.VALUE,
            source_seqs=(1,),
            input_version=session.input_version,
        )

        self.assertIn(ReasonCode.VALUE_DIVERGENCE_UNRESOLVED, draft_blockers(session))
        problem = validate_assignments(session, {Role.PRODUCT: ("I-V",)})
        self.assertIsNotNone(problem)
        self.assertEqual(problem[0], ReasonCode.ISSUE_NEEDS_USER)

    def test_challenger_value_issue_is_marked_for_user(self) -> None:
        session = build_session(self.engine)
        issue = Issue(
            issue_id="I-V",
            topic_id="T1",
            raised_by=Role.CHALLENGER,
            summary="免费与付费的取舍",
            severity=Severity.MAJOR,
            kind=DivergenceKind.VALUE,
            source_seqs=(1,),
        )
        task = SpeakerTask(
            task_id="t-challenge",
            session_id=session.session_id,
            role=Role.CHALLENGER,
            kind=TaskKind.CHALLENGE,
            phase=Phase.CHALLENGE,
            round_kind=RoundKind.FIRST,
            round_no=1,
            topic_id="T1",
            snapshot_ref=session.snapshot_ref,
            input_version=session.input_version,
        )
        output = SpeakerOutput(
            task_id=task.task_id,
            session_id=session.session_id,
            role=Role.CHALLENGER,
            kind=TaskKind.CHALLENGE,
            input_version=session.input_version,
            snapshot_ref=session.snapshot_ref,
            round_no=1,
            topic_id="T1",
            text="需要用户决定",
            source_seqs=(1,),
            raised_issues=(issue,),
        )

        self.engine._bookkeep(session, task, output, 2)

        self.assertTrue(session.issues["I-V"].needs_user)


class DraftDivergenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = DiscussionEngine()

    def test_draft_keeps_divergence_record(self) -> None:
        session = build_session(self.engine)
        item = session.current_item()
        session.covered_statements = {item.topic_id: set(item.required_roles)}
        session.challenged_topics = {item.topic_id}
        session.phase = Phase.DRAFTING
        session.waiting_for = WaitingFor.ROLE_RESULTS
        task = SpeakerTask(
            task_id="t-draft",
            session_id=session.session_id,
            role=Role.RECORDER,
            kind=TaskKind.DRAFT,
            phase=Phase.DRAFTING,
            round_kind=RoundKind.FIRST,
            round_no=1,
            topic_id="T1",
            snapshot_ref=session.snapshot_ref,
            input_version=session.input_version,
        )
        session.tasks[task.task_id] = task
        output = SpeakerOutput(
            task_id=task.task_id,
            session_id=session.session_id,
            role=Role.RECORDER,
            kind=TaskKind.DRAFT,
            input_version=session.input_version,
            snapshot_ref=session.snapshot_ref,
            round_no=1,
            topic_id="T1",
            source_seqs=(1,),
            item_refs=("FR-01",),
            draft_content={"schema_version": "1.0", "requirements": [{"id": "FR-01"}]},
            agreements=("首版只做查空闲与预约",),
            alternatives=("按楼层分时段开放",),
            disagreements=("是否支持匿名预约尚未达成一致",),
        )

        decision = self.engine.submit_output(session, output)

        self.assertTrue(decision.ok, decision.code)
        draft = session.current_draft
        self.assertIsNotNone(draft)
        self.assertEqual(draft.agreements, ("首版只做查空闲与预约",))
        self.assertEqual(draft.alternatives, ("按楼层分时段开放",))
        self.assertEqual(draft.disagreements, ("是否支持匿名预约尚未达成一致",))


if __name__ == "__main__":
    unittest.main()
