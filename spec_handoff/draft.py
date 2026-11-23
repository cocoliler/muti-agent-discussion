"""候选草稿契约与 `DesignSpecContent` 转换。

记录员（`Role.RECORDER`）提交的 `draft_content` 是讨论层的自由映射，本模块把它固定成
可校验的草稿契约，并转成 FlowStudio v3.1 的 `DesignSpecContent`：

- 进入 `DesignSpecContent` 的字段与设计文档 6.1 的映射表一一对应；
- 草稿额外携带的 `source_seqs`、`covers`、`pain_points`、`risks`、`removed_requirement_ids`
  只用于追溯与校验，**不会**写进导出正文（正文不允许未知字段）。

草稿契约（记录员输出正文）::

    {
      "product_name": "…", "background": "…",
      "goals": ["…"], "target_users": ["…"],
      "requirements": [{
        "id": "FR-01", "kind": "functional", "priority": "must",
        "text": "…", "acceptance_criteria": ["…"],
        "source_seqs": [2, 3, 4],      # 该需求依据的已提交消息序号
        "covers": ["P-1", "R-1"]       # 覆盖的痛点 / 风险 ID
      }],
      "technical_constraints": [], "development_constraints": [],
      "delivery_requirements": ["…"], "acceptance_criteria": ["…"],
      "supplemental_text": "", "confirmed_assumptions": [],
      "pain_points": [{"id": "P-1", "text": "…", "confirmed": true}],
      "risks": [{"id": "R-1", "text": "…", "must_handle": true}],
      "removed_requirement_ids": []
    }

`id` 与 `source_seqs` 是追溯键：修改正文保留 ID，新增需求用新 ID，删除需求必须在
`removed_requirement_ids` 中记录，避免下游旧计划引用漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .codes import Code, Finding

#: 顶层字段进入 DesignSpecContent 的顺序，与契约 Schema 的 required 顺序一致。
CONTENT_FIELDS: tuple[str, ...] = (
    "schema_version",
    "product_name",
    "background",
    "goals",
    "target_users",
    "requirements",
    "technical_constraints",
    "development_constraints",
    "delivery_requirements",
    "acceptance_criteria",
    "supplemental_text",
    "confirmed_assumptions",
)

#: 草稿需求中进入正文的字段。
REQUIREMENT_FIELDS: tuple[str, ...] = ("id", "kind", "priority", "text", "acceptance_criteria")

_KINDS = frozenset({"functional", "nonfunctional"})
_PRIORITIES = frozenset({"must", "should", "could"})


@dataclass(frozen=True)
class DraftRequirement:
    """草稿中的一条需求：正文 + 追溯键。"""

    requirement_id: str
    kind: str
    priority: str
    text: str
    acceptance_criteria: tuple[str, ...] = ()
    source_seqs: tuple[int, ...] = ()
    covers: tuple[str, ...] = ()

    def to_content(self) -> dict[str, Any]:
        return {
            "id": self.requirement_id,
            "kind": self.kind,
            "priority": self.priority,
            "text": self.text,
            "acceptance_criteria": list(self.acceptance_criteria),
        }


@dataclass(frozen=True)
class PainPoint:
    """用户痛点。只有 `confirmed=True` 的痛点必须被需求覆盖。"""

    item_id: str
    text: str
    confirmed: bool = True


@dataclass(frozen=True)
class Risk:
    """风险。`must_handle=True` 表示评审要求必须处理，需有可观察验收条件。"""

    item_id: str
    text: str
    must_handle: bool = True


@dataclass(frozen=True)
class DraftSpec:
    """记录员候选稿视图。字段与 `DesignSpecContent` 对齐，另加追溯信息。"""

    product_name: str = ""
    background: str = ""
    goals: tuple[str, ...] = ()
    target_users: tuple[str, ...] = ()
    requirements: tuple[DraftRequirement, ...] = ()
    technical_constraints: tuple[str, ...] = ()
    development_constraints: tuple[str, ...] = ()
    delivery_requirements: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    supplemental_text: str = ""
    confirmed_assumptions: tuple[str, ...] = ()
    pain_points: tuple[PainPoint, ...] = ()
    risks: tuple[Risk, ...] = ()
    removed_requirement_ids: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = field(default=(), compare=False, repr=False)

    # ------------------------------------------------------------------ 读取
    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(item.requirement_id for item in self.requirements)

    def requirement(self, requirement_id: str) -> DraftRequirement | None:
        for item in self.requirements:
            if item.requirement_id == requirement_id:
                return item
        return None

    # ------------------------------------------------------------------ 转换
    def to_content(self) -> dict[str, Any]:
        """转成 `DesignSpecContent` 正文；不含追溯字段。"""

        return {
            "schema_version": "1.0",
            "product_name": self.product_name,
            "background": self.background,
            "goals": list(self.goals),
            "target_users": list(self.target_users),
            "requirements": [item.to_content() for item in self.requirements],
            "technical_constraints": list(self.technical_constraints),
            "development_constraints": list(self.development_constraints),
            "delivery_requirements": list(self.delivery_requirements),
            "acceptance_criteria": list(self.acceptance_criteria),
            "supplemental_text": self.supplemental_text,
            "confirmed_assumptions": list(self.confirmed_assumptions),
        }

    # ------------------------------------------------------------------ 解析
    @classmethod
    def parse(cls, content: Mapping[str, Any] | None) -> DraftSpec:
        """宽松解析：尽量构造草稿，把问题以 `findings` 返回，交给校验层统一汇报。"""

        if content is None:
            return cls(findings=(Finding(Code.SPEC_REQUIRED, "/", "候选稿缺少正文内容"),))
        if not isinstance(content, Mapping):
            return cls(
                findings=(Finding(Code.INVALID_VALUE, "/", "候选稿正文必须是对象"),)
            )

        findings: list[Finding] = []
        if str(content.get("schema_version", "1.0")).strip() != "1.0":
            findings.append(
                Finding(Code.INVALID_VALUE, "/schema_version", "只支持 schema_version \"1.0\"")
            )

        return cls(
            product_name=_text(content, "product_name", findings),
            background=_text(content, "background", findings),
            goals=_texts(content, "goals", findings),
            target_users=_texts(content, "target_users", findings),
            requirements=_requirements(content.get("requirements"), findings),
            technical_constraints=_texts(content, "technical_constraints", findings),
            development_constraints=_texts(content, "development_constraints", findings),
            delivery_requirements=_texts(content, "delivery_requirements", findings),
            acceptance_criteria=_texts(content, "acceptance_criteria", findings),
            supplemental_text=_optional_text(content, "supplemental_text", findings),
            confirmed_assumptions=_texts(content, "confirmed_assumptions", findings),
            pain_points=_pain_points(content.get("pain_points"), findings),
            risks=_risks(content.get("risks"), findings),
            removed_requirement_ids=_plain_strings(
                content.get("removed_requirement_ids"), "/removed_requirement_ids", findings
            ),
            findings=tuple(findings),
        )


# --------------------------------------------------------------------- 解析辅助


def _text(source: Mapping[str, Any], key: str, findings: list[Finding]) -> str:
    value = source.get(key)
    if not isinstance(value, str):
        findings.append(Finding(Code.SPEC_REQUIRED, f"/{key}", "缺少必填文本字段"))
        return ""
    return value


def _optional_text(source: Mapping[str, Any], key: str, findings: list[Finding]) -> str:
    value = source.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        findings.append(Finding(Code.INVALID_VALUE, f"/{key}", "必须是文本"))
        return ""
    return value


def _texts(source: Mapping[str, Any], key: str, findings: list[Finding]) -> tuple[str, ...]:
    value = source.get(key)
    if value is None:
        if key in {"goals", "target_users", "delivery_requirements", "acceptance_criteria"}:
            findings.append(Finding(Code.SPEC_REQUIRED, f"/{key}", "缺少必填列表"))
        return ()
    return _plain_strings(value, f"/{key}", findings)


def _plain_strings(value: Any, path: str, findings: list[Finding]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        findings.append(Finding(Code.INVALID_VALUE, path, "必须是字符串数组"))
        return ()
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            findings.append(Finding(Code.INVALID_VALUE, f"{path}/{index}", "必须是文本"))
            continue
        items.append(item)
    return tuple(items)


def _seqs(value: Any, path: str, findings: list[Finding]) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        findings.append(Finding(Code.INVALID_VALUE, path, "必须是消息序号数组"))
        return ()
    seqs: list[int] = []
    for index, item in enumerate(value):
        if not isinstance(item, int) or isinstance(item, bool) or item < 1:
            findings.append(
                Finding(Code.INVALID_VALUE, f"{path}/{index}", "必须是大于 0 的整数序号")
            )
            continue
        seqs.append(item)
    return tuple(seqs)


def _requirements(value: Any, findings: list[Finding]) -> tuple[DraftRequirement, ...]:
    if value is None:
        findings.append(Finding(Code.SPEC_REQUIRED, "/requirements", "缺少需求列表"))
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        findings.append(Finding(Code.INVALID_VALUE, "/requirements", "必须是对象数组"))
        return ()

    requirements: list[DraftRequirement] = []
    for index, item in enumerate(value):
        path = f"/requirements/{index}"
        if not isinstance(item, Mapping):
            findings.append(Finding(Code.INVALID_VALUE, path, "必须是对象"))
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            findings.append(Finding(Code.SPEC_REQUIRED, f"{path}/id", "需求 ID 不能为空"))
            continue
        kind = item.get("kind", "functional")
        if kind not in _KINDS:
            findings.append(
                Finding(Code.INVALID_VALUE, f"{path}/kind", "可选值为 functional、nonfunctional")
            )
            kind = "functional"
        priority = item.get("priority", "must")
        if priority not in _PRIORITIES:
            findings.append(
                Finding(Code.INVALID_VALUE, f"{path}/priority", "可选值为 must、should、could")
            )
            priority = "must"
        text = item.get("text")
        if not isinstance(text, str):
            findings.append(Finding(Code.SPEC_REQUIRED, f"{path}/text", "需求正文不能为空"))
            text = ""
        requirements.append(
            DraftRequirement(
                requirement_id=identifier.strip(),
                kind=str(kind),
                priority=str(priority),
                text=text,
                acceptance_criteria=_plain_strings(
                    item.get("acceptance_criteria", []), f"{path}/acceptance_criteria", findings
                ),
                source_seqs=_seqs(item.get("source_seqs", []), f"{path}/source_seqs", findings),
                covers=_plain_strings(item.get("covers", []), f"{path}/covers", findings),
            )
        )
    return tuple(requirements)


def _pain_points(value: Any, findings: list[Finding]) -> tuple[PainPoint, ...]:
    items: list[PainPoint] = []
    for index, item in enumerate(_mappings(value, "/pain_points", findings)):
        path = f"/pain_points/{index}"
        items.append(
            PainPoint(
                item_id=_item_id(item, path, findings),
                text=_item_text(item, path, findings),
                confirmed=bool(item.get("confirmed", True)),
            )
        )
    return tuple(items)


def _risks(value: Any, findings: list[Finding]) -> tuple[Risk, ...]:
    items: list[Risk] = []
    for index, item in enumerate(_mappings(value, "/risks", findings)):
        path = f"/risks/{index}"
        items.append(
            Risk(
                item_id=_item_id(item, path, findings),
                text=_item_text(item, path, findings),
                must_handle=bool(item.get("must_handle", True)),
            )
        )
    return tuple(items)


def _mappings(value: Any, path: str, findings: list[Finding]) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        findings.append(Finding(Code.INVALID_VALUE, path, "必须是对象数组"))
        return ()
    items: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            findings.append(Finding(Code.INVALID_VALUE, f"{path}/{index}", "必须是对象"))
            continue
        items.append(item)
    return tuple(items)


def _item_id(item: Mapping[str, Any], path: str, findings: list[Finding]) -> str:
    value = item.get("id")
    if not isinstance(value, str) or not value.strip():
        findings.append(Finding(Code.SPEC_REQUIRED, f"{path}/id", "追溯条目 ID 不能为空"))
        return ""
    return value.strip()


def _item_text(item: Mapping[str, Any], path: str, findings: list[Finding]) -> str:
    value = item.get("text")
    if not isinstance(value, str) or not value.strip():
        findings.append(Finding(Code.SPEC_REQUIRED, f"{path}/text", "追溯条目正文不能为空"))
        return ""
    return value


__all__ = [
    "CONTENT_FIELDS",
    "REQUIREMENT_FIELDS",
    "DraftRequirement",
    "DraftSpec",
    "PainPoint",
    "Risk",
]
