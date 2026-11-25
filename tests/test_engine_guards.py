"""编排防线测试：轮次硬上限、价值分歧转人工、分歧记录随草稿留存。

这些是把设计稿里「无限循环」「假共识」「分歧记录」三条落成代码后的回归。
"""

from __future__ import annotations

import unittest
from dataclasses import replace

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
from discussion_orchestration.models import IssueResponse
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
        session.directed_rounds = session.max_directed_rounds = 2

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
        session.revision_rounds = session.max_revision_rounds = 2
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


    def test_incomplete_combined_response_and_unverified_pass_are_rejected(self):
        session = build_session(self.engine)
        session.tasks.clear()
        session.waiting_for = WaitingFor.HOST_DECISION
        for issue_id in ("blocking-A", "blocking-B"):
            session.issues[issue_id] = Issue(issue_id, "T1", Role.REVIEWER, "需要验证",
                                             Severity.BLOCKING, source_seqs=(1,))
        dispatched = self.engine.name_responses(session, {Role.PRODUCT: tuple(session.issues)})
        task, = dispatched.tasks
        first = IssueResponse("blocking-A", "已处理 A", "验证 A 的输出", (1,))
        output = SpeakerOutput(task.task_id, session.session_id, task.role, task.kind, task.input_version,
                               task.snapshot_ref, task.round_no, task.topic_id, text="修改两个问题",
                               issue_ids=("blocking-A",), responses=(first,))
        self.assertFalse(self.engine.submit_output(session, output).ok)
        self.assertTrue(all(issue.status.value == "open" for issue in session.issues.values()))
        second = IssueResponse("blocking-B", "已处理 B", "检查 B 的结果", (1,))
        accepted = replace(output, issue_ids=("blocking-A", "blocking-B"), responses=(first, second))
        self.assertTrue(self.engine.submit_output(session, accepted).ok)
        self.assertTrue(all(issue.status.value == "answered" for issue in session.issues.values()))
        session.tasks.clear()
        draft = DraftRevision("d-proof", 1, session.input_version, {})
        session.drafts.append(draft)
        session.status = SessionStatus.REVIEWING
        session.waiting_for = WaitingFor.REVIEW
        review = ReviewResult("r-proof", session.session_id, draft.draft_id, session.input_version, Verdict.PASS)
        self.assertFalse(self.engine.submit_review(session, review).ok)
        findings = tuple(
            ReviewFinding(issue_id, Severity.BLOCKING, "FR-1", "已核对冻结草稿", "结果可观察",
                          Role.PRODUCT, issue_id=issue_id, source_seqs=(1,))
            for issue_id in session.issues
        )
        self.assertTrue(self.engine.submit_review(session, replace(review, verified_findings=findings)).ok)
        self.assertTrue(all(issue.status.value == "closed" for issue in session.issues.values()))


    def test_returning_a_passed_draft_creates_a_targeted_user_revision(self):
        session = build_session(self.engine)
        session.tasks.clear()
        session.status = SessionStatus.REVIEWING
        session.waiting_for = WaitingFor.REVIEW
        draft = DraftRevision("d-approved", 1, session.input_version, {})
        session.drafts.append(draft)
        review = ReviewResult("r-approved", session.session_id, draft.draft_id, session.input_version, Verdict.PASS)
        self.assertTrue(self.engine.submit_review(session, review).ok)
        self.assertFalse(self.engine.require_changes(session, "", draft_id=draft.draft_id).ok)

        returned = self.engine.require_changes(session, "增加并发冲突提示", draft_id=draft.draft_id)
        self.assertTrue(returned.ok)
        task, = returned.tasks
        issue_id, = task.issue_ids
        issue = session.issues[issue_id]
        self.assertEqual(issue.raised_by, Role.USER)
        self.assertEqual(issue.summary, "增加并发冲突提示")
        self.assertEqual(session.messages[-1].item_refs, (draft.draft_id,))
        self.assertEqual(session.round_kind, RoundKind.REVISION)

    def test_cosmetic_changes_stall_but_new_acceptance_rules_do_not(self):
        session = build_session(self.engine)
        finding = ReviewFinding("issue-1", Severity.BLOCKING, "FR-1", "验收含糊", "说明验收条件", Role.PRODUCT)
        for version in (1, 2, 3):
            draft = DraftRevision(
                f"draft-{version}", version, session.input_version,
                {"product_name": f"项目 {version}", "requirements": [
                    {"id": "FR-1", "text": "预约会议室", "acceptance_criteria": ["确认成功。"]},
                ]},
            )
            review = ReviewResult(
                f"review-{version}", session.session_id, draft.draft_id, session.input_version, Verdict.REVISE,
                findings=(replace(finding, finding_id=f"issue-{version}"),),
            )
            session.drafts.append(draft)
            if version < 3:
                session.reviews.append(review)
        self.assertTrue(self.engine._revision_stalled(session, draft, review))
        draft.content["requirements"][0]["acceptance_criteria"] = ["并发时只能产生一个有效预约。"]
        self.assertFalse(self.engine._revision_stalled(session, draft, review))

        # A -> B -> A is also a stall, even if the reviewer renumbers findings
        # and the recorder changes an unrelated specification field.
        session.drafts[1].content["requirements"][0]["acceptance_criteria"] = ["一次只允许预订一间。"]
        draft.content["requirements"][0]["acceptance_criteria"] = ["确认成功。"]
        draft.content["technical_constraints"] = ["使用不同缓存键。"]
        self.assertTrue(self.engine._revision_stalled(session, draft, review))


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
