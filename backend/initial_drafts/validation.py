"""Deterministic publication checks. Optional semantic findings are advisory."""

from collections.abc import Iterable, Mapping
from graphlib import CycleError, TopologicalSorter
from typing import Literal

from pydantic import Field, ValidationError, computed_field, model_validator

from .contracts import ClarificationQuestion, DraftItem, DraftModel, HardConstraint, Sources, Text, WorkingDraft
from .policy import DEFAULT_DRAFT_POLICY, STRUCTURE_GATE_REF, DraftPolicy
from .template import CORE_FIELD_KINDS, DEFAULT_SOFTWARE_TEMPLATE, DraftTemplate

SEMANTIC_CHECKLIST = {
    "core_goal": "是否回应用户核心目标",
    "module_fit": "模块划分是否合理",
    "module_overlap": "模块职责是否重复",
    "scope_drift": "是否存在严重范围偏移",
    "flow_coverage": "核心业务流程是否完整",
    "risk_coverage": "是否遗漏重要风险",
    "system_boundary": "系统边界是否合理",
}


class EvidenceContext(DraftModel):
    session_id: Text
    input_version: int = Field(gt=0, strict=True)
    visible_source_seqs: Sources
    confirmed_source_seqs: Sources = ()
    required_constraints: tuple[HardConstraint, ...] = ()
    pending_questions: tuple[ClarificationQuestion, ...] = ()

    @model_validator(mode="after")
    def check_trusted_sources(self):
        visible = set(self.visible_source_seqs)
        confirmed = set(self.confirmed_source_seqs)
        if not confirmed <= visible:
            raise ValueError("trusted confirmation sources must also be visible")
        if len({constraint.id for constraint in self.required_constraints}) != len(self.required_constraints):
            raise ValueError("trusted constraint IDs must be unique")
        for constraint in self.required_constraints:
            allowed = confirmed if constraint.origin == "user" else visible
            if not set(constraint.source_seqs) <= allowed:
                raise ValueError(f"unavailable trusted source for constraint {constraint.id}")
        if len({question.question_id for question in self.pending_questions}) != len(self.pending_questions):
            raise ValueError("trusted question IDs must be unique")
        if any(not set(question.source_seqs) <= visible for question in self.pending_questions):
            raise ValueError("trusted questions require visible sources")
        return self


class SemanticFinding(DraftModel):
    criterion: Literal["core_goal", "module_fit", "module_overlap", "scope_drift",
                       "flow_coverage", "risk_coverage", "system_boundary"]
    path: Text
    message: Text
    suggestion: Text


class GateFinding(DraftModel):
    rule_id: str
    path: str
    severity: Literal["warning", "error", "blocker"]
    message: str
    suggestion: str

    @computed_field
    @property
    def blocks_publication(self) -> bool:
        return self.severity != "warning"


class GateResult(DraftModel):
    findings: tuple[GateFinding, ...]

    @computed_field
    @property
    def status(self) -> Literal["PASS", "PASS_WITH_WARNINGS", "INVALID", "BLOCKED"]:
        severities = {finding.severity for finding in self.findings}
        if "error" in severities:
            return "INVALID"
        if "blocker" in severities:
            return "BLOCKED"
        return "PASS_WITH_WARNINGS" if severities else "PASS"

    @computed_field
    @property
    def can_publish(self) -> bool:
        return not any(finding.blocks_publication for finding in self.findings)


def _pointer(*parts) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _evidence_nodes(draft: WorkingDraft):
    yield (), draft
    if draft.content.positioning:
        yield ("content", "positioning"), draft.content.positioning
    for name in CORE_FIELD_KINDS:
        values = getattr(draft.content, name)
        if not isinstance(values, tuple):
            continue
        for index, item in enumerate(values):
            path = ("content", name, index)
            yield path, item
            if name == "business_flows":
                for step_index, step in enumerate(item.steps):
                    yield (*path, "steps", step_index), step


class InitialDraftQualityGate:
    ref = STRUCTURE_GATE_REF

    def evaluate(
        self, raw: WorkingDraft | Mapping, context: EvidenceContext, *,
        template: DraftTemplate = DEFAULT_SOFTWARE_TEMPLATE, policy: DraftPolicy = DEFAULT_DRAFT_POLICY,
        asked_ids: Iterable[str] = (), semantic_findings: Iterable[SemanticFinding] = (),
    ) -> GateResult:
        findings = []

        def add(rule_id, path, message, suggestion, severity="error"):
            findings.append(GateFinding(rule_id=rule_id, path=path, severity=severity,
                                        message=message, suggestion=suggestion))

        try:
            draft = WorkingDraft.model_validate(raw)
        except ValidationError as exc:
            for error in exc.errors(include_input=False, include_context=False, include_url=False):
                add(f"contract.{error['type']}", _pointer(*error["loc"]), error["msg"], "按工作草稿字段定义修正。")
            return GateResult(findings=tuple(findings))

        content = draft.content
        if draft.session_id != context.session_id:
            add("draft.session", "/session_id", "草稿属于其他会话。", "使用当前会话的输入和来源重新生成。")
        if draft.input_version != context.input_version:
            add("draft.snapshot", "/input_version", "草稿与当前输入版本不匹配。", "基于当前输入重新生成。")
        if draft.template_ref != template.ref:
            add("draft.template_version", "/template_ref", "草稿使用了其他模板版本。", "使用当前模板版本重新生成。")
        if draft.policy_ref != policy.ref or policy.quality_gate != self.ref:
            add("draft.policy_version", "/policy_ref", "生成策略或质量门禁版本不匹配。", "使用匹配的版本配置。")

        optional_extensions = {field.id for field in template.fields if field.id not in CORE_FIELD_KINDS}
        for field_id in sorted(content.custom_sections.keys() - optional_extensions):
            add("template.unknown_field", _pointer("content", "custom_sections", field_id),
                "模板中没有定义此扩展字段。", "移除该字段或在模板中定义。")
        for field_id in sorted(set(policy.required_views) - {field.id for field in template.fields}):
            add("template.missing_view", _pointer("content", field_id), "策略要求的视图在模板中不存在。", "选用匹配的模板与策略。")
        for field in template.fields:
            core = field.id in CORE_FIELD_KINDS
            value = getattr(content, field.id) if core else content.custom_sections.get(field.id)
            path = _pointer("content", field.id) if core else _pointer("content", "custom_sections", field.id)
            required = field.required or "non_empty" in field.acceptance_rules or field.id in policy.required_views
            if required and not value:
                add("template.required", path, f"缺少必填内容「{field.title}」。", field.description)
            if not core and value is not None and not isinstance(value, str if field.kind == "text" else tuple):
                add("template.field_type", path, f"扩展字段必须为 {field.kind}。", field.description)
            if "unique" in field.acceptance_rules and isinstance(value, tuple) and field.id != "modules":
                keys = [getattr(item, "id", getattr(item, "question_id", item)) for item in value]
                if len(set(keys)) != len(keys):
                    add("template.unique", path, f"「{field.title}」中有重复值。", "合并重复项并保留稳定标识。")

        self._check_graph(content, add)
        visible = set(context.visible_source_seqs)
        confirmed = set(context.confirmed_source_seqs)
        envelope_sources = set(draft.source_seqs)
        for path, item in _evidence_nodes(draft):
            sources = set(item.source_seqs)
            if not sources <= visible:
                add("evidence.invalid_reference", _pointer(*path, "source_seqs"), "引用了当前快照不可见的消息。",
                    "仅引用当前快照已授权的消息。")
            elif not sources <= envelope_sources:
                add("evidence.not_in_draft", _pointer(*path, "source_seqs"), "逐项依据缺少草稿级索引。",
                    "把条目引用添加到草稿的来源清单。")
            if isinstance(item, DraftItem) and item.status == "confirmed" and not (sources and sources <= confirmed):
                add("evidence.confirmation_required", _pointer(*path, "status"), "已确认内容缺少可信确认依据。",
                    "补充用户确认依据，或标记为假设、建议或待确认。")
        self._check_constraints(content.constraints, context.required_constraints, add)

        questions = {question.question_id: question for question in content.open_questions}
        if len(questions) != len(content.open_questions):
            add("clarification.duplicate_id", "/content/open_questions", "待确认问题的 ID 重复。", "为每个问题设置唯一 ID。")
        question_paths = {question.question_id: _pointer("content", "open_questions", index)
                          for index, question in enumerate(content.open_questions)}
        for trusted in context.pending_questions:
            recorded = questions.get(trusted.question_id)
            if recorded and (recorded.category != trusted.category or recorded.question != trusted.question):
                add("clarification.changed_question", question_paths[trusted.question_id], "待确认问题与可信快照不一致。",
                    "保留原问题和分类。")
            questions[trusted.question_id] = trusted
        plan = policy.clarification_policy().plan(questions.values(), asked_ids=asked_ids)
        for question in questions.values():
            path = question_paths.get(question.question_id, "/content/open_questions")
            if question.category == "blocking":
                add("clarification.unresolved_blocker", path, question.question,
                    plan.pause_reason or "补充阻断信息。", "blocker")
            elif question.category == "important":
                add("clarification.after_draft", path, question.question,
                    "在草稿中记录并在发布后集中确认。", "warning")
        for finding in semantic_findings:
            add(f"semantic.{finding.criterion}", finding.path, finding.message, finding.suggestion, "warning")
        return GateResult(findings=tuple(findings))

    @staticmethod
    def _check_graph(content, add):
        modules = {module.id: module for module in content.modules}
        if len(modules) != len(content.modules):
            add("modules.duplicate_id", "/content/modules", "模块 ID 重复。", "为不同模块分配唯一的稳定 ID。")
        actors = {actor.id for actor in content.actors}
        if len(actors) != len(content.actors):
            add("actors.duplicate_id", "/content/actors", "用户角色 ID 重复。", "为不同角色分配唯一的稳定 ID。")

        def check_refs(refs, available, path):
            unknown = set(refs) - available
            if unknown:
                add("reference.unknown_id", path, f"未知标识：{', '.join(sorted(unknown))}", "更正引用或补齐相应条目。")

        module_ids = set(modules)
        for index, module in enumerate(content.modules):
            check_refs(module.depends_on, module_ids, _pointer("content", "modules", index, "depends_on"))
            if module.parent_id:
                check_refs((module.parent_id,), module_ids, _pointer("content", "modules", index, "parent_id"))
        for field, graph in (
            ("depends_on", {mid: module_ids.intersection(module.depends_on) for mid, module in modules.items()}),
            ("parent_id", {mid: (module.parent_id,) if module.parent_id in modules else () for mid, module in modules.items()}),
        ):
            try:
                tuple(TopologicalSorter(graph).static_order())
            except CycleError:
                add("modules.cycle", _pointer("content", "modules", field), "模块关系存在循环。", "调整层级或依赖以打破循环。")
        for index, scenario in enumerate(content.scenarios):
            check_refs(scenario.actor_ids, actors, _pointer("content", "scenarios", index, "actor_ids"))
        for index, flow in enumerate(content.business_flows):
            for step_index, step in enumerate(flow.steps):
                path = ("content", "business_flows", index, "steps", step_index)
                check_refs((step.module_id,), module_ids, _pointer(*path, "module_id"))
                check_refs((step.actor_id,), actors, _pointer(*path, "actor_id"))
        for index, question in enumerate(content.open_questions):
            check_refs(question.affected_modules, module_ids, _pointer("content", "open_questions", index, "affected_modules"))

    @staticmethod
    def _check_constraints(actual, expected, add):
        expected_by_id = {constraint.id: constraint for constraint in expected}
        seen = set()
        for index, constraint in enumerate(actual):
            path = _pointer("content", "constraints", index)
            original = expected_by_id.get(constraint.id)
            if constraint.id in seen:
                add("constraints.duplicate_id", path, "硬约束 ID 重复。", "保留唯一的原始约束记录。")
            elif original is None or (constraint.text, constraint.origin, constraint.rule_ref, set(constraint.source_seqs)) != (
                    original.text, original.origin, original.rule_ref, set(original.source_seqs)):
                add("constraints.unverified", path, "硬约束与可信输入快照不一致。", "保留约束原文、类型和来源。")
            seen.add(constraint.id)
        for missing in sorted(expected_by_id.keys() - seen):
            add("constraints.missing", "/content/constraints", f"缺少硬约束 {missing}。", "按原文与来源补回此约束。")
