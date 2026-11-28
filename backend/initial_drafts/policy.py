"""Programmatic generation settings for a later integration."""

from collections.abc import Iterable
from typing import Literal

from pydantic import Field, model_validator

from .clarification import ClarificationMode, ClarificationPlan, ClarificationPolicy
from .contracts import ClarificationQuestion, DraftModel, Identifier, Text, VersionRef
from .template import DEFAULT_SOFTWARE_TEMPLATE, DraftTemplate

STRUCTURE_GATE_REF = VersionRef(id="initial-draft-structure", version=1)


class GenerationPlan(DraftModel):
    policy_ref: VersionRef
    template: DraftTemplate
    generation_mode: Literal["outline_first", "specialist_first"]
    target_depth: Literal["outline", "expanded"]
    required_views: tuple[Identifier, ...]
    specialist_review_timing: Literal["after_outline", "before_outline"]
    quality_gate: VersionRef
    output_token_budget: int
    requirements: tuple[Text, ...]
    clarification: ClarificationPlan


class DraftPolicy(DraftModel):
    ref: VersionRef = VersionRef(id="outline-first", version=1)
    generation_mode: Literal["outline_first", "specialist_first"] = "outline_first"
    target_depth: Literal["outline", "expanded"] = "outline"
    required_views: tuple[Identifier, ...] = ("goals", "modules", "business_flows")
    max_initial_questions: int = Field(default=3, ge=0, strict=True)
    clarification_mode: ClarificationMode = "blocking_before_draft"
    specialist_review_timing: Literal["after_outline", "before_outline"] = "after_outline"
    quality_gate: VersionRef = STRUCTURE_GATE_REF
    output_token_budget: int = Field(default=4096, gt=0, strict=True)

    @model_validator(mode="after")
    def check_switches(self):
        if (self.generation_mode == "outline_first") != (self.specialist_review_timing == "after_outline"):
            raise ValueError("generation mode and specialist review timing disagree")
        if len(set(self.required_views)) != len(self.required_views):
            raise ValueError("required views must be unique")
        return self

    def clarification_policy(self) -> ClarificationPolicy:
        return ClarificationPolicy(max_initial_questions=self.max_initial_questions, mode=self.clarification_mode)

    def plan(self, questions: Iterable[ClarificationQuestion] = (), *, template: DraftTemplate = DEFAULT_SOFTWARE_TEMPLATE,
             asked_ids: Iterable[str] = ()) -> GenerationPlan:
        unknown_views = set(self.required_views) - {field.id for field in template.fields}
        if unknown_views:
            raise ValueError(f"required views are missing from template: {', '.join(sorted(unknown_views))}")
        return GenerationPlan(
            policy_ref=self.ref, template=template, generation_mode=self.generation_mode, target_depth=self.target_depth,
            required_views=self.required_views, specialist_review_timing=self.specialist_review_timing,
            quality_gate=self.quality_gate, output_token_budget=self.output_token_budget,
            requirements=(
                "优先遵守用户硬性约束和企业强制设计要求，保留原文和来源。",
                "优先说明全局结构、核心模块和主流程。避免过早选定技术栈或编造企业规则。",
                "假设和设计建议保持各自状态，只有有依据的内容才可标为已确认。",
                *template.generation_requirements,
            ),
            clarification=self.clarification_policy().plan(questions, asked_ids=asked_ids),
        )


DEFAULT_DRAFT_POLICY = DraftPolicy()
