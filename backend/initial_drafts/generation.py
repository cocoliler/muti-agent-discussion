"""Task input, model output and the server-owned working-draft envelope."""

from datetime import datetime, timezone
import re
from typing import Literal

from pydantic import Field

from .contracts import ClarificationQuestion, DraftModel, HardConstraint, Identifier, Sources, Text, WorkingDraft, WorkingDraftContent, working_draft_id
from .policy import DraftPolicy
from .template import DraftTemplate
from .validation import EvidenceContext, InitialDraftQualityGate


class InitialDraftTask(DraftModel):
    snapshot_ref: Text
    template: DraftTemplate
    policy: DraftPolicy
    evidence: EvidenceContext
    asked_ids: tuple[Identifier, ...] = ()
    previous_questions: tuple[ClarificationQuestion, ...] = ()
    clarification_seq: int = 0


class ClarificationResolution(DraftModel):
    question_id: Identifier
    reason: Text
    source_seqs: Sources


class InitialDraftOutput(DraftModel):
    action: Literal["ASK_USER", "CREATE_OUTLINE"]
    text: Text
    plain_summary: Text | None = None
    source_seqs: Sources
    questions: tuple[ClarificationQuestion, ...] = ()
    content: WorkingDraftContent | None = None
    target_roles: tuple[Identifier, ...] = ()
    discussion_focus: Text | None = None
    discussion_targets: tuple[Identifier, ...] = Field(default=(), max_length=12)
    resolved_questions: tuple[ClarificationResolution, ...] = ()


def explicit_user_constraints(messages) -> tuple[HardConstraint, ...]:
    return tuple(
        HardConstraint(id=f"USER-{message.seq}-{index}", text=match[1].strip(),
                       origin="user", source_seqs=(message.seq,))
        for message in messages
        for index, match in enumerate(re.finditer(
            r"(?:^|[。；;\n])\s*(?:硬性约束|约束|constraints?)\s*[:：]\s*([^。；;\n]+)",
            message.text, re.IGNORECASE,
        ), 1)
        if match[1].strip()
    )


def prepare_working_draft(request, output: InitialDraftOutput):
    task = request.initial_task
    context = task.evidence
    # A model may also extract an unlabelled user constraint. Only a verbatim
    # quotation from the stated user evidence can enter the trusted input set.
    trusted = {item.id: item for item in context.required_constraints}
    user_texts = {message.seq: message.text for message in request.messages if message.role == "user"}
    for constraint in output.content.constraints:
        if (constraint.id not in trusted and constraint.origin == "user"
                and all(constraint.text in user_texts.get(seq, "") for seq in constraint.source_seqs)):
            trusted[constraint.id] = constraint
    context = EvidenceContext.model_validate({**context.model_dump(), "required_constraints": tuple(trusted.values())})
    draft = WorkingDraft(
        session_id=request.session_id, draft_id=working_draft_id(request.session_id, request.input_version, 1),
        input_version=request.input_version, revision=1, created_at=datetime.now(timezone.utc),
        snapshot_ref=task.snapshot_ref, template_ref=task.template.ref, policy_ref=task.policy.ref,
        source_seqs=output.source_seqs, content=output.content,
    )
    result = InitialDraftQualityGate().evaluate(draft, context, template=task.template, policy=task.policy,
                                                asked_ids=task.asked_ids)
    return draft, result


def outline_preview(content: WorkingDraftContent) -> dict:
    """Project structured data into the existing card and specialist context."""
    names = {module.id: module.name for module in content.modules}
    return {
        "product_name": content.product_name,
        "positioning": content.positioning.text,
        "target_users": [item.text for item in content.actors],
        "core_modules": [f"{item.name} · {item.text}" for item in content.modules],
        "business_flow": [f"{step.text} → {step.outcome}" for flow in content.business_flows for step in flow.steps],
        "system_boundary": [item.text for item in content.boundaries],
        "assumptions": [item.text for item in content.assumptions],
        "open_questions": [item.question for item in content.open_questions],
        "goals": [item.text for item in content.goals],
        "scenarios": [f"{item.trigger} → {item.outcome}" for item in content.scenarios],
        "scope_in": [item.text for item in content.scope_in],
        "scope_out": [item.text for item in content.scope_out],
        "module_relations": [
            f"{item.name} 依赖 {names[dependency]}"
            for item in content.modules for dependency in item.depends_on
        ],
        "external_dependencies": [item.text for item in content.external_dependencies],
        "constraints": [item.text for item in content.constraints],
        "risks": [item.text for item in content.risks],
    }
