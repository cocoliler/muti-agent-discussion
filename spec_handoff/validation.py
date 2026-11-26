"""交付校验：结构、需求 ID、来源覆盖与验收条件。

设计文档 6.1 要求「除结构校验外，交付前检查引用真实存在、已确认的范围内痛点有需求覆盖、
须处理的关键风险和整改项有可观察的验收条件」。本模块把这三件事做成确定性检查：

1. **结构**：复用 `schema.check_content`，与 FlowStudio 导入同一份 Schema；
2. **追溯**：需求必须引用本会话已提交的消息序号，覆盖已确认痛点与须处理风险；
3. **验收**：每条需求至少一条非空验收条件，同一需求内不得重复；
4. **版本**：需求 ID 跨修订保持稳定，删除需显式登记。

来源追溯需要「已提交消息序号」：给会话快照时取自 `session.messages`，也可直接传 `known_seqs`；
两者都缺省时（例如直接校验一份已定稿规格）跳过追溯，只做结构与验收检查。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .codes import Code, Finding, Level, blocking
from .draft import DraftSpec
from .export import check_export


@dataclass(frozen=True)
class DeliveryReport:
    """一次交付校验的结果。`ok` 为真才允许签收或导出。"""

    content: Mapping[str, Any]
    findings: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        return not blocking(self.findings)

    @property
    def errors(self) -> tuple[Finding, ...]:
        return blocking(self.findings)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.level is Level.WARNING)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [item.as_dict() for item in self.errors],
            "warnings": [item.as_dict() for item in self.warnings],
        }


def check_acceptance(draft: DraftSpec) -> tuple[Finding, ...]:
    """验收条件必须可观察：非空、不重复。"""

    findings: list[Finding] = []
    for index, requirement in enumerate(draft.requirements):
        path = f"/requirements/{index}/acceptance_criteria"
        criteria = [item.strip() for item in requirement.acceptance_criteria]
        if not any(criteria):
            findings.append(
                Finding(
                    Code.ACCEPTANCE_MISSING,
                    path,
                    "每条需求至少需要一条验收条件",
                    requirement_id=requirement.requirement_id,
                )
            )
            continue
        seen: set[str] = set()
        for position, criterion in enumerate(criteria):
            if not criterion:
                findings.append(
                    Finding(
                        Code.ACCEPTANCE_MISSING,
                        f"{path}/{position}",
                        "验收条件不能为空",
                        requirement_id=requirement.requirement_id,
                    )
                )
                continue
            if criterion in seen:
                findings.append(
                    Finding(
                        Code.ACCEPTANCE_DUPLICATE,
                        f"{path}/{position}",
                        "同一需求内验收条件重复",
                        requirement_id=requirement.requirement_id,
                    )
                )
            seen.add(criterion)
    return tuple(findings)


def check_traceability(draft: DraftSpec, known_seqs: Sequence[int]) -> tuple[Finding, ...]:
    """来源必须真实存在；已确认痛点与须处理风险必须有需求覆盖。"""

    known = set(known_seqs)
    trace_ids = {item.item_id for item in draft.pain_points} | {
        item.item_id for item in draft.risks
    }
    findings: list[Finding] = []
    covered: set[str] = set()

    for index, requirement in enumerate(draft.requirements):
        path = f"/requirements/{index}"
        if not requirement.source_seqs:
            findings.append(
                Finding(
                    Code.SOURCE_MISSING,
                    f"{path}/source_seqs",
                    "需求必须引用本会话中已提交的发言作为依据",
                    requirement_id=requirement.requirement_id,
                )
            )
        for position, seq in enumerate(requirement.source_seqs):
            if seq not in known:
                findings.append(
                    Finding(
                        Code.SOURCE_UNKNOWN,
                        f"{path}/source_seqs/{position}",
                        f"消息 {seq} 不属于当前会话或尚未提交",
                        requirement_id=requirement.requirement_id,
                    )
                )
        for position, item_id in enumerate(requirement.covers):
            if item_id not in trace_ids:
                findings.append(
                    Finding(
                        Code.UNKNOWN_TRACE_ITEM,
                        f"{path}/covers/{position}",
                        f"痛点或风险 {item_id} 不在这份草稿的追溯清单中",
                        requirement_id=requirement.requirement_id,
                    )
                )
            else:
                covered.add(item_id)

    for index, pain_point in enumerate(draft.pain_points):
        if pain_point.confirmed and pain_point.item_id not in covered:
            findings.append(
                Finding(
                    Code.REQUIRED_UNCOVERED,
                    f"/pain_points/{index}",
                    f"已确认痛点 {pain_point.item_id} 没有需求覆盖",
                )
            )

    for index, risk in enumerate(draft.risks):
        if not risk.must_handle:
            continue
        if risk.item_id not in covered:
            findings.append(
                Finding(
                    Code.REQUIRED_UNCOVERED,
                    f"/risks/{index}",
                    f"须处理风险 {risk.item_id} 没有需求覆盖",
                )
            )
            continue
        for position, requirement in enumerate(draft.requirements):
            if risk.item_id not in requirement.covers:
                continue
            if not any(item.strip() for item in requirement.acceptance_criteria):
                findings.append(
                    Finding(
                        Code.ACCEPTANCE_MISSING,
                        f"/requirements/{position}/acceptance_criteria",
                        f"风险 {risk.item_id} 的整改缺少可观察验收条件",
                        requirement_id=requirement.requirement_id,
                    )
                )
    return tuple(findings)


def check_id_stability(draft: DraftSpec, parent: DraftSpec | None) -> tuple[Finding, ...]:
    """需求 ID 跨修订保持稳定；被删除的需求必须在 `removed_requirement_ids` 登记。"""

    if parent is None:
        return ()
    current = set(draft.requirement_ids)
    previous = set(parent.requirement_ids)
    declared = set(draft.removed_requirement_ids)
    findings: list[Finding] = []

    for identifier in sorted(previous - current - declared):
        findings.append(
            Finding(
                Code.ID_DROPPED,
                "/requirements",
                f"需求 {identifier} 在上一版存在但当前版本缺失，请在 removed_requirement_ids 登记删除",
                requirement_id=identifier,
            )
        )
    for identifier in sorted(declared & current):
        findings.append(
            Finding(
                Code.INVALID_VALUE,
                "/removed_requirement_ids",
                f"需求 {identifier} 已登记删除，但仍存在于当前版本",
                requirement_id=identifier,
            )
        )
    for identifier in sorted(declared - previous):
        findings.append(
            Finding(
                Code.INVALID_VALUE,
                "/removed_requirement_ids",
                f"删除登记 {identifier} 在上一版中不存在",
                level=Level.WARNING,
                requirement_id=identifier,
            )
        )
    return tuple(findings)


def build_report(
    draft: DraftSpec,
    *,
    session: Any | None = None,
    known_seqs: Sequence[int] | None = None,
    parent: DraftSpec | None = None,
) -> DeliveryReport:
    """汇总全部交付校验。

    来源追溯需要已提交消息序号：给 `session` 时取其消息序号，也可直接给 `known_seqs`；
    两者都缺省时跳过追溯，只做结构与验收检查。
    """

    content = draft.to_content()
    findings: list[Finding] = list(draft.findings)
    findings.extend(check_export(content))
    findings.extend(check_acceptance(draft))
    findings.extend(check_id_stability(draft, parent))
    seqs: Sequence[int] | None = known_seqs
    if session is not None:
        seqs = [message.seq for message in session.messages]
    if seqs is not None:
        findings.extend(check_traceability(draft, seqs))
    return DeliveryReport(content=content, findings=tuple(findings))


def parent_draft(drafts: Sequence[Any], draft: Any) -> DraftSpec | None:
    """按 `parent_id` 找到上一版草稿并解析，供 ID 稳定性检查复用。"""

    parent_id = getattr(draft, "parent_id", None)
    if parent_id is None:
        return None
    for candidate in drafts:
        if candidate.draft_id == parent_id:
            return DraftSpec.parse(candidate.content)
    return None


__all__ = [
    "DeliveryReport",
    "build_report",
    "check_acceptance",
    "check_id_stability",
    "check_traceability",
    "parent_draft",
]
