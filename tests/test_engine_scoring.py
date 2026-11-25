"""收敛打分测试：五维加权、权衡分歧强制打分、评分卡落库与可追溯。"""

from __future__ import annotations

import unittest
from dataclasses import replace

from discussion_orchestration import (
    Agenda,
    AgendaItem,
    DivergenceKind,
    DiscussionEngine,
    Issue,
    OptionScore,
    Phase,
    ReasonCode,
    Role,
    Scorecard,
    ScoreDimension,
    SessionStatus,
    Severity,
    SpeakerOutput,
    TaskKind,
    WaitingFor,
    draft_blockers,
)
from discussion_orchestration.models import OptionStance
from discussion_orchestration.scoring import rank_options, validate
from discussion_orchestration.validation import validate_assignments

IDEA = "名称：会议室预约。用户：行政人员。目标：预约会议室。"


def build_session(engine: DiscussionEngine):
    session = engine.create_session(session_id="s-score", project_id="p-score", idea=IDEA)
    agenda = Agenda(
        summary="明确首版范围",
        items=(AgendaItem(topic_id="T1", title="预约与冲突", question="如何避免重复预约？"),),
    )
    decision = engine.submit_agenda(session, agenda)
    assert decision.ok, decision.code
    # 首轮发言与质疑视为已完成，直接进入主持人调度。
    session.tasks.clear()
    session.waiting_for = WaitingFor.HOST_DECISION
    item = session.current_item()
    session.covered_statements = {item.topic_id: set(item.required_roles)}
    session.challenged_topics = {item.topic_id}
    return session


def tradeoff_issue(session) -> Issue:
    return Issue(
        issue_id="I-T",
        topic_id=session.topic_id,
        raised_by=Role.CHALLENGER,
        summary="按楼层分时段开放，还是全量开放",
        severity=Severity.MAJOR,
        kind=DivergenceKind.TRADEOFF,
        source_seqs=(1,),
        input_version=session.input_version,
    )


def scorecard(rationale: str = "优先用户价值") -> Scorecard:
    return Scorecard(
        options=(
            OptionScore(option_id="A", title="全量开放", scores={
                ScoreDimension.USER_VALUE: 5, ScoreDimension.FEASIBILITY: 4,
                ScoreDimension.COST: 3, ScoreDimension.DIFFERENTIATION: 3, ScoreDimension.RISK: 3,
            }),
            OptionScore(option_id="B", title="按楼层分时段开放", scores={
                ScoreDimension.USER_VALUE: 3, ScoreDimension.FEASIBILITY: 5,
                ScoreDimension.COST: 5, ScoreDimension.DIFFERENTIATION: 2, ScoreDimension.RISK: 4,
            }),
        ),
        rationale=rationale,
    )


class ScoringMathTest(unittest.TestCase):
    def test_weighted_ranking_is_deterministic(self) -> None:
        ranked = rank_options(scorecard().options)

        self.assertEqual([item.option_id for item in ranked], ["A", "B"])
        self.assertEqual([item.rank for item in ranked], [1, 2])
        self.assertAlmostEqual(ranked[0].total, 3.85, places=2)
        self.assertAlmostEqual(ranked[1].total, 3.80, places=2)

    def test_invalid_scores_are_rejected(self) -> None:
        bad = Scorecard(options=(
            OptionScore(option_id="A", title="A", scores={ScoreDimension.USER_VALUE: 6}),
            OptionScore(option_id="B", title="B", scores={ScoreDimension.USER_VALUE: 1}),
        ))

        problem = validate(bad.options, bad.weights)

        self.assertIsNotNone(problem)
        self.assertEqual(problem[0], ReasonCode.INVALID_SCORECARD)


class TradeoffScoringFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = DiscussionEngine()
        self.session = build_session(self.engine)
        self.session.issues["I-T"] = tradeoff_issue(self.session)

    def test_tradeoff_issue_cannot_be_named(self) -> None:
        problem = validate_assignments(self.session, {Role.PRODUCT: ("I-T",)})

        self.assertIsNotNone(problem)
        self.assertEqual(problem[0], ReasonCode.SCORING_REQUIRED)
        # 权衡分歧本身不阻断成稿门禁的结构项，但成稿前必须先打分。
        self.assertNotIn(ReasonCode.VALUE_DIVERGENCE_UNRESOLVED, draft_blockers(self.session))

    def test_draft_request_dispatches_scoring_task(self) -> None:
        decision = self.engine.submit_candidate_draft(self.session)

        self.assertTrue(decision.ok, decision.code)
        self.assertEqual(decision.detail["scoring_required"], True)
        self.assertEqual([task.kind for task in decision.tasks], [TaskKind.SCORING])
        self.assertEqual(decision.tasks[0].role, Role.HOST)
        self.assertEqual(self.session.waiting_for, WaitingFor.ROLE_RESULTS)

    def test_scorecard_is_recorded_and_draft_references_it(self) -> None:
        dispatched = self.engine.submit_candidate_draft(self.session)
        task = dispatched.tasks[0]
        output = SpeakerOutput(
            task_id=task.task_id,
            session_id=self.session.session_id,
            role=Role.HOST,
            kind=TaskKind.SCORING,
            input_version=self.session.input_version,
            snapshot_ref=self.session.snapshot_ref,
            round_no=task.round_no,
            topic_id=task.topic_id,
            text="按五维打分",
            source_seqs=(1,),
            scorecard=scorecard(),
        )

        scored = self.engine.submit_output(self.session, output)

        self.assertTrue(scored.ok, scored.code)
        self.assertEqual(len(self.session.scorecards), 1)
        self.assertAlmostEqual(self.session.scorecards[0].weights[ScoreDimension.USER_VALUE], 0.30, places=2)
        self.assertEqual(self.session.issues["I-T"].status.value, "open")
        self.assertEqual(self.session.waiting_for, WaitingFor.ROLE_RESULTS)
        self.assertTrue(scored.tasks)
        for stance_task in scored.tasks:
            self.assertTrue(all(seq < self.session.messages[-1].seq for seq in stance_task.message_seqs))
            stance = replace(output, task_id=stance_task.task_id, role=stance_task.role, kind=TaskKind.STANCE,
                             scorecard=None, stances=tuple(
                                 OptionStance(option.option_id, "conditional", "需明确验证条件", (1,))
                                 for option in self.session.scorecards[0].options
                             ))
            self.assertTrue(self.engine.submit_output(self.session, stance).ok)
        self.assertEqual(self.session.waiting_for, WaitingFor.HOST_DECISION)
        self.assertEqual(set(self.session.scorecards[0].stances), {task.role for task in scored.tasks})
        self.assertEqual(self.session.chosen_option_id, "A")
        self.assertEqual(self.session.issues["I-T"].status.value, "closed")
        self.assertIn("采纳 A", self.session.messages[-1].text)

        drafting = self.engine.submit_candidate_draft(self.session)

        self.assertTrue(drafting.ok, drafting.code)
        self.assertEqual([task.kind for task in drafting.tasks], [TaskKind.DRAFT])
        self.assertEqual(self.session.status, SessionStatus.DISCUSSING)
        self.assertEqual(self.session.phase, Phase.DRAFTING)

    def test_bad_scorecard_is_rejected_without_side_effects(self) -> None:
        dispatched = self.engine.submit_candidate_draft(self.session)
        task = dispatched.tasks[0]
        broken = Scorecard(options=(
            OptionScore(option_id="A", title="A", scores={ScoreDimension.USER_VALUE: 9}),
            OptionScore(option_id="A", title="B", scores={ScoreDimension.USER_VALUE: 1}),
        ))
        output = SpeakerOutput(
            task_id=task.task_id,
            session_id=self.session.session_id,
            role=Role.HOST,
            kind=TaskKind.SCORING,
            input_version=self.session.input_version,
            snapshot_ref=self.session.snapshot_ref,
            round_no=task.round_no,
            topic_id=task.topic_id,
            text="错误评分",
            source_seqs=(1,),
            scorecard=broken,
        )

        rejected = self.engine.submit_output(self.session, output)

        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.code, ReasonCode.INVALID_SCORECARD)
        self.assertEqual(self.session.scorecards, [])
        self.assertIsNone(self.session.chosen_option_id)


if __name__ == "__main__":
    unittest.main()
