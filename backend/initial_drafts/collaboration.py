"""Scoped review inputs and deterministic updates of WorkingDraft revisions."""

from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from .contracts import DraftItem, DraftModel, Identifier, Sources, Text, WorkingDraft, working_draft_id
from .validation import EvidenceContext, InitialDraftQualityGate


ROLE_SECTIONS = {
    "product": ("goals", "actors", "scope_in", "scope_out", "modules"),
    "architect": ("modules", "boundaries", "external_dependencies", "business_flows"),
    "designer": ("actors", "scenarios", "business_flows", "modules"),
    "research": ("assumptions", "open_questions", "risks"),
    "market": ("scope_in", "scope_out", "external_dependencies", "assumptions"),
    "critic": ("risks", "boundaries", "open_questions", "business_flows"),
}
ANCHOR_SECTIONS = ("modules", "business_flows", "boundaries", "assumptions", "risks", "open_questions", "constraints")


class DraftSuggestion(DraftModel):
    target_id: Identifier
    operation: Literal["add", "update", "delete", "retain"]
    proposed_change: Text
    rationale: Text
    severity: Literal["advisory", "major", "blocking"] = "advisory"
    source_seqs: Sources = Field(min_length=1)
    requires_user_confirmation: bool = False


class SubmittedSuggestion(DraftSuggestion):
    suggestion_id: Identifier
    agent_role: Text
    draft_id: Identifier
    base_revision: int


class DraftDecision(DraftModel):
    suggestion_id: Identifier
    decision: Literal["accept", "reject", "defer"]
    reason: Text


class DraftEntry(DraftModel):
    target_id: Identifier
    section: str
    content: dict[str, Any]


class WorkingDraftReview(DraftModel):
    draft_id: Identifier
    revision: int
    product_name: str | None
    goals: tuple[DraftEntry, ...]
    topic_refs: tuple[Identifier, ...]
    entries: tuple[DraftEntry, ...]
    constraints: tuple[DraftEntry, ...]
    assumptions: tuple[DraftEntry, ...]
    suggestions: tuple[SubmittedSuggestion, ...] = ()
    suggestion_message_seqs: dict[Identifier, int] = Field(default_factory=dict)

    @property
    def target_ids(self) -> set[str]:
        return {item.target_id for item in (*self.entries, *self.goals, *self.constraints, *self.assumptions)}


def target_index(content, *, strict=True):
    index = {}
    ambiguous = set()
    for section in ("positioning", "goals", "actors", "scenarios", *ANCHOR_SECTIONS,
                    "scope_in", "scope_out", "external_dependencies", "suggestions"):
        values = getattr(content, section)
        if section == "positioning":
            values = (values,) if values else ()
        for item in values:
            key = item.question_id if section == "open_questions" else item.id
            if key in index:
                if strict:
                    raise ValueError(f"ambiguous working draft target: {key}")
                ambiguous.add(key)
                del index[key]
            elif key not in ambiguous:
                index[key] = (section, item)
    return index


def agenda_refs(content, requested):
    index = target_index(content)
    if requested:
        if len(set(requested)) != len(requested) or set(requested) - index.keys():
            raise ValueError("discussion_targets must be unique IDs in the working draft")
        return tuple(requested)
    return tuple(next(key for key, (section, _) in index.items() if section == name)
                 for name in ANCHOR_SECTIONS if any(section == name for section, _ in index.values()))


def pending_suggestions(draft, messages, topic_id, round_no=None):
    return tuple(
        (SubmittedSuggestion.model_validate(raw), message.seq)
        for message in messages if message.topic_id == topic_id and message.input_version == draft.input_version
        and (round_no is None or message.round_no == round_no)
        for raw in message.draft_suggestions
        if raw["draft_id"] == draft.draft_id and raw["base_revision"] == draft.revision
    )


def build_review(draft, role, refs, pending=(), *, for_candidate=False):
    index = target_index(draft.content, strict=False)
    refs = tuple(ref for ref in refs if ref in index)
    sections = ROLE_SECTIONS.get(role, ())
    requested = {suggestion.target_id for suggestion, _ in pending}
    if for_candidate:
        requested.update(index)
    for ref in refs:
        section, item = index[ref]
        if section in sections or role in {"host", "critic", "scribe"}:
            requested.add(ref)
            if section == "modules":
                requested.update((*item.depends_on, item.parent_id))
            elif section == "business_flows":
                requested.update(step.module_id for step in item.steps)
            elif section == "open_questions":
                requested.update(item.affected_modules)

    def entry(key):
        section, item = index[key]
        return DraftEntry(target_id=key, section=section, content=item.model_dump(mode="json"))

    secondary = tuple(key for key, (section, _) in index.items() if section in sections and key not in requested)
    entries = tuple(entry(key) for key in index if key in requested)
    return WorkingDraftReview(
        draft_id=draft.draft_id, revision=draft.revision, product_name=draft.content.product_name, topic_refs=refs,
        goals=tuple(entry(item.id) for item in draft.content.goals if item.id in index),
        constraints=tuple(DraftEntry(target_id=item.id, section="constraints", content=item.model_dump(mode="json"))
                          for item in draft.content.constraints),
        assumptions=tuple(entry(item.id) for item in draft.content.assumptions
                          if item.id in index and (item.id in refs or role == "research")),
        entries=(*entries, *(entry(key) for key in secondary[:6])),
        suggestions=tuple(item for item, _ in pending),
        suggestion_message_seqs={item.suggestion_id: seq for item, seq in pending},
    )


def bind_suggestion(suggestion, task_id, role, review, ordinal):
    return SubmittedSuggestion(
        **suggestion, suggestion_id=f"suggestion-{uuid5(NAMESPACE_URL, f'{task_id}:{ordinal}')}",
        agent_role=role, draft_id=review.draft_id, base_revision=review.revision,
    )


def conflicting_targets(suggestions):
    changes = {}
    for suggestion in suggestions:
        if suggestion.operation in {"update", "delete"}:
            changes.setdefault(suggestion.target_id, set()).add((suggestion.operation, suggestion.proposed_change))
    return {key for key, options in changes.items() if len(options) > 1}


def revise_working_draft(session, output, task, seq):
    """Resolve only validated advisory changes. Preserve the original on any invalid merge."""
    previous = session.initial_draft
    draft = WorkingDraft.model_validate(previous.content)
    cutoff = max(task.message_seqs, default=0)
    superseded = {message.replaces_answer_seq for message in session.messages if message.replaces_answer_seq is not None}
    visible = [message for message in session.messages if message.seq <= cutoff and message.seq not in superseded]
    pending = pending_suggestions(draft, visible, output.topic_id)
    if not pending:
        return
    decisions = {entry.suggestion_id: entry for entry in map(DraftDecision.model_validate, output.draft_decisions)}
    index = target_index(draft.content, strict=False)
    conflicts = conflicting_targets(suggestion for suggestion, _ in pending)
    content = draft.content.model_dump(mode="json")
    evidence = set(draft.source_seqs)
    visible_seqs = {message.seq for message in visible}
    linked_issues = [issue for issue in session.issues.values() if issue.input_version == draft.input_version
                     and issue.topic_id == task.topic_id]
    edits = []
    applied = set()
    for suggestion, message_seq in pending:
        decision = decisions.get(suggestion.suggestion_id)
        status, reason = (decision.decision, decision.reason) if decision else ("defer", "尚未采纳，留待后续处理。")
        section, item = index.get(suggestion.target_id, (None, None))
        if section is None:
            status, reason = "reject", "该条目没有唯一的工作草稿标识。"
        if status == "accept" and (suggestion.requires_user_confirmation or suggestion.severity != "advisory"
                                   or suggestion.target_id in conflicts
                                   or any(issue.status.value == "open" and suggestion.target_id in issue.draft_refs
                                          for issue in linked_issues)):
            status, reason = "defer", "需先通过原有议题处理机制解决。"
        if status == "accept" and suggestion.operation != "retain":
            if section in {"constraints", "open_questions"} or (isinstance(item, DraftItem) and item.status == "confirmed"):
                status, reason = "reject", "保留硬约束、用户确认内容与待确认问题。"
            elif not set(suggestion.source_seqs) <= visible_seqs:
                status, reason = "reject", "修改依据已不在任务快照内。"
            elif suggestion.target_id in applied:
                status, reason = "reject", "此条目已有采纳的修改。"
            else:
                if suggestion.operation == "add":
                    content["suggestions"].append({
                        "id": suggestion.suggestion_id, "status": "proposed",
                        "text": f"{suggestion.target_id} · {suggestion.proposed_change}",
                        "source_seqs": list(suggestion.source_seqs),
                    })
                elif section == "positioning":
                    if suggestion.operation == "delete":
                        content[section] = None
                    else:
                        content[section]["text"] = suggestion.proposed_change
                        content[section]["source_seqs"] = list(dict.fromkeys((*item.source_seqs, *suggestion.source_seqs)))
                else:
                    slot = next(entry for entry in content[section] if entry["id"] == suggestion.target_id)
                    if suggestion.operation == "delete":
                        content[section].remove(slot)
                    else:
                        slot["text"] = suggestion.proposed_change
                        slot["source_seqs"] = list(dict.fromkeys((*item.source_seqs, *suggestion.source_seqs)))
                applied.add(suggestion.target_id)
                evidence.update(suggestion.source_seqs)
        evidence.add(message_seq)
        edits.append({"suggestion": suggestion.model_dump(mode="json"), "message_seq": message_seq,
                      "decision": status, "reason": reason})

    revision = draft.revision + 1
    raw = draft.model_dump(mode="json")
    raw.update(draft_id=working_draft_id(draft.session_id, draft.input_version, revision), revision=revision, content=content,
               snapshot_ref=task.snapshot_ref, source_seqs=sorted(evidence), created_at=datetime.now(timezone.utc))
    context = EvidenceContext(session_id=draft.session_id, input_version=draft.input_version,
                              visible_source_seqs=sorted(visible_seqs),
                              confirmed_source_seqs=tuple(m.seq for m in visible if m.role.value == "user"),
                              required_constraints=draft.content.constraints, pending_questions=draft.content.open_questions)
    report = InitialDraftQualityGate().evaluate(raw, context)
    if not report.can_publish:
        # Preserve the version and report which decisions could not be committed.
        return {"base_draft_id": draft.draft_id, "base_revision": draft.revision, "edits": edits,
                "quality_gate_result": report.model_dump(mode="json")}
    revised = WorkingDraft.model_validate(raw)
    session.previous_outlines.append(previous)
    session.initial_draft = replace(
        revised.as_working_revision(), parent_id=draft.draft_id, publication_seq=seq,
        quality_gate_result=report.model_dump(mode="json"),
        working_changes={"base_draft_id": draft.draft_id, "base_revision": draft.revision,
                         "draft_id": revised.draft_id, "revision": revised.revision,
                         "edits": edits, "touched_target_ids": sorted(applied),
                         "open_issue_ids": [issue.issue_id for issue in linked_issues if issue.status.value != "closed"]},
    )
