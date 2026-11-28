"""Working artifacts have their own identity and lifecycle."""

from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

from discussion_orchestration.models import DraftRevision

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$", max_length=128)]
Positive = Annotated[StrictInt, Field(gt=0)]
Sources = tuple[Positive, ...]
ContentStatus = Literal["confirmed", "proposed", "assumed", "open"]
QuestionCategory = Literal["blocking", "important", "deferred"]


class DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True, revalidate_instances="always")


class VersionRef(DraftModel):
    id: Identifier
    version: Positive


class Evidence(DraftModel):
    source_seqs: Sources = ()


class DraftItem(Evidence):
    id: Identifier
    text: Text
    status: ContentStatus


class Assumption(DraftItem):
    status: Literal["assumed"] = "assumed"


class Suggestion(DraftItem):
    status: Literal["proposed"] = "proposed"


class Module(DraftItem):
    name: Text
    parent_id: Identifier | None = None
    depends_on: tuple[Identifier, ...] = ()


class Scenario(DraftItem):
    actor_ids: tuple[Identifier, ...] = Field(min_length=1)
    trigger: Text
    outcome: Text


class FlowStep(DraftItem):
    module_id: Identifier
    actor_id: Identifier
    outcome: Text


class BusinessFlow(DraftItem):
    steps: tuple[FlowStep, ...] = Field(min_length=1)


class ClarificationQuestion(Evidence):
    question_id: Identifier
    question: Text
    category: QuestionCategory
    affected_modules: tuple[Identifier, ...] = ()
    reason: Text
    suggested_options: tuple[Text, ...] = ()


class HardConstraint(Evidence):
    id: Identifier
    text: Text
    origin: Literal["user", "enterprise"]
    rule_ref: Identifier | None = None

    @model_validator(mode="after")
    def check_origin(self):
        if self.origin == "user" and (not self.source_seqs or self.rule_ref is not None):
            raise ValueError("user constraints require message evidence")
        if self.origin == "enterprise" and self.rule_ref is None:
            raise ValueError("enterprise constraints require a trusted rule reference")
        return self


class WorkingDraftContent(DraftModel):
    product_name: Text | None = None
    positioning: DraftItem | None = None
    goals: tuple[DraftItem, ...] = ()
    actors: tuple[DraftItem, ...] = ()
    scenarios: tuple[Scenario, ...] = ()
    modules: tuple[Module, ...] = ()
    business_flows: tuple[BusinessFlow, ...] = ()
    scope_in: tuple[DraftItem, ...] = ()
    scope_out: tuple[DraftItem, ...] = ()
    boundaries: tuple[DraftItem, ...] = ()
    external_dependencies: tuple[DraftItem, ...] = ()
    constraints: tuple[HardConstraint, ...] = ()
    suggestions: tuple[Suggestion, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    risks: tuple[DraftItem, ...] = ()
    open_questions: tuple[ClarificationQuestion, ...] = ()
    custom_sections: dict[Identifier, Text | tuple[Text, ...]] = Field(default_factory=dict)


def working_draft_id(session_id: str, input_version: int, revision: int) -> str:
    if not session_id.strip() or min(input_version, revision) < 1:
        raise ValueError("working draft identity requires a session and positive versions")
    identity = uuid5(NAMESPACE_URL, f"muti-agent-discussion/working/{input_version}/{revision}/{session_id.strip()}")
    return f"working-{identity}"


def working_module_id(session_id: str, stable_key: str) -> str:
    if not session_id.strip() or not stable_key.strip():
        raise ValueError("module identity requires a session and stable key")
    namespace = uuid5(NAMESPACE_URL, f"muti-agent-discussion/modules/{session_id.strip()}")
    return f"mod-{uuid5(namespace, stable_key.strip())}"


class WorkingDraft(Evidence):
    source_seqs: Sources = Field(min_length=1)
    session_id: Text
    draft_id: Identifier
    input_version: Positive
    revision: Positive
    schema_version: Literal[2] = 2
    lifecycle: Literal["working"] = "working"
    snapshot_ref: Text | None = None
    template_ref: VersionRef
    policy_ref: VersionRef
    created_at: AwareDatetime
    content: WorkingDraftContent

    @model_validator(mode="after")
    def check_identity(self):
        if self.draft_id != working_draft_id(self.session_id, self.input_version, self.revision):
            raise ValueError("draft_id does not match session, input version and revision")
        return self

    def as_working_revision(self) -> DraftRevision:
        return DraftRevision(
            draft_id=self.draft_id, version=0, input_version=self.input_version,
            content=self.model_dump(mode="json"), source_seqs=self.source_seqs,
            current=False, working_schema_version=self.schema_version,
        )
