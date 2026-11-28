"""Budgeted planning for already identified information gaps."""

from collections.abc import Iterable
from typing import Literal

from pydantic import Field

from .contracts import ClarificationQuestion, DraftModel, QuestionCategory, VersionRef

ClarificationMode = Literal["blocking_before_draft", "manual"]


def classify_question(*, prevents_safe_outline: bool, affects_important_decision: bool) -> QuestionCategory:
    if prevents_safe_outline:
        return "blocking"
    return "important" if affects_important_decision else "deferred"


class ClarificationPlan(DraftModel):
    ask_now: tuple[ClarificationQuestion, ...]
    waiting_on: tuple[ClarificationQuestion, ...]
    unasked_blocking: tuple[ClarificationQuestion, ...]
    after_draft: tuple[ClarificationQuestion, ...]
    deferred: tuple[ClarificationQuestion, ...]
    publication_blocked: bool
    pause_reason: str | None = None


class ClarificationPolicy(DraftModel):
    ref: VersionRef = VersionRef(id="clarification", version=1)
    max_initial_questions: int = Field(default=3, ge=0, strict=True)
    mode: ClarificationMode = "blocking_before_draft"

    def plan(self, questions: Iterable[ClarificationQuestion], *, asked_ids: Iterable[str] = ()) -> ClarificationPlan:
        pending = tuple(questions)
        if len({question.question_id for question in pending}) != len(pending):
            raise ValueError("clarification question IDs must be unique")
        asked = set(asked_ids)
        blocking = tuple(question for question in pending if question.category == "blocking")
        waiting_on = tuple(question for question in blocking if question.question_id in asked)
        new = tuple(question for question in blocking if question.question_id not in asked)
        remaining = max(0, self.max_initial_questions - len(asked))
        ask_now = new[:remaining] if self.mode == "blocking_before_draft" else ()
        unasked = new[len(ask_now):]
        reason = None
        if blocking:
            if unasked:
                reason = ("仍有阻断问题需要人工确认。" if self.mode == "manual"
                          else "初稿提问额度不足，仍有阻断问题需要补充。")
            else:
                reason = "等待阻断问题的回答。"
        return ClarificationPlan(
            ask_now=ask_now, waiting_on=waiting_on, unasked_blocking=unasked,
            after_draft=tuple(question for question in pending if question.category == "important"),
            deferred=tuple(question for question in pending if question.category == "deferred"),
            publication_blocked=bool(blocking), pause_reason=reason,
        )
