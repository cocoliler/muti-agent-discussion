"""Declarative requirements for the working-draft generator and gate."""

from typing import Literal

from pydantic import Field, model_validator

from .contracts import DraftModel, Identifier, Text, VersionRef

FieldKind = Literal["text", "item", "items", "modules", "scenarios", "flows", "constraints", "questions", "text_list"]
AcceptanceRule = Literal["non_empty", "unique"]

CORE_FIELD_KINDS: dict[str, FieldKind] = {
    "product_name": "text", "positioning": "item", "goals": "items", "actors": "items",
    "scenarios": "scenarios", "modules": "modules", "business_flows": "flows", "scope_in": "items",
    "scope_out": "items", "boundaries": "items", "external_dependencies": "items", "constraints": "constraints",
    "suggestions": "items", "assumptions": "items", "risks": "items", "open_questions": "questions",
}


class TemplateField(DraftModel):
    id: Identifier
    kind: FieldKind
    title: Text
    description: Text
    required: bool = False
    level: int = Field(default=1, ge=1, le=3, strict=True)
    acceptance_rules: tuple[AcceptanceRule, ...] = ()


class DraftTemplate(DraftModel):
    ref: VersionRef
    fields: tuple[TemplateField, ...] = Field(min_length=1)
    generation_requirements: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def check_fields(self):
        seen = set()
        for field in self.fields:
            if field.id in seen:
                raise ValueError(f"duplicate template field {field.id}")
            expected = CORE_FIELD_KINDS.get(field.id)
            if field.id == "custom_sections" or (expected and field.kind != expected):
                raise ValueError(f"invalid kind for core field {field.id}")
            if expected is None and field.kind not in {"text", "text_list"}:
                raise ValueError(f"custom section {field.id} must use text or text_list")
            if "unique" in field.acceptance_rules and field.kind in {"text", "item"}:
                raise ValueError(f"unique requires a list field: {field.id}")
            seen.add(field.id)
        return self


DEFAULT_SOFTWARE_TEMPLATE = DraftTemplate(
    ref=VersionRef(id="software-design", version=1),
    fields=tuple(TemplateField(
        id=key, kind=CORE_FIELD_KINDS[key], title=title, description=description,
        required=required, level=level, acceptance_rules=("unique",) if key == "modules" else (),
    ) for key, title, description, required, level in (
        ("product_name", "项目名称", "使用用户给出的名称或标识清晰的暂定名称。", True, 1),
        ("positioning", "产品定位", "说明解决的问题及服务对象。", True, 1),
        ("goals", "项目目标", "说明用户要达到的可观察结果。", True, 1),
        ("actors", "目标用户", "列出主要用户角色和职责。", True, 1),
        ("scenarios", "使用场景", "说明角色、触发条件和预期结果。", True, 2),
        ("scope_in", "首版范围", "列出首版提供的能力。", True, 2),
        ("scope_out", "范围之外", "列出已知暂缓或排除的能力。", False, 2),
        ("modules", "模块层级与依赖", "分配稳定模块 ID，用 parent_id 表示层级，用 depends_on 表示依赖。", True, 1),
        ("business_flows", "核心业务流程", "按发生顺序列出角色、模块、操作及可观察结果。", True, 1),
        ("boundaries", "系统边界", "明确系统负责的工作和职责交接点。", True, 1),
        ("external_dependencies", "外部依赖", "列出已知或待验证的外部依赖及影响。", False, 2),
        ("constraints", "强制约束", "逐条保留输入快照中的用户和企业硬约束及其来源。", False, 1),
        ("suggestions", "设计建议", "记录可调整的方案和取舍。", False, 2),
        ("assumptions", "初步假设", "明确未经确认的推断。", False, 2),
        ("risks", "风险", "记录影响、依据与待验证事项。", False, 2),
        ("open_questions", "待确认事项", "保留未回答的问题、影响模块和确认原因。", False, 2),
    )),
    generation_requirements=("先覆盖整体框架及主流程。", "没有依据的条目标为建议、假设或待确认。"),
)
