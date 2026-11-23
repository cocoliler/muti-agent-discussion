"""DesignSpecContent 结构校验。

规则唯一来源是仓库内的 `tech/artifacts/schema/design-spec.schema.json`（与 FlowStudio v3.1
契约模块同版）。这里只实现该文件实际用到的 JSON Schema 关键字，语义与 FlowStudio 前端导入
校验、后端 Pydantic 严格模型对齐：

- 未知字段直接拒绝（`additionalProperties: false`）；
- 字符串先 trim 再校验长度与标识符模式；
- 字符长度按 Unicode 码点计，禁止控制字符（保留 Tab / LF / CR）；
- 需求 ID 在同一版本内唯一。

本模块不做业务判断，也不读取会话状态。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .codes import Code, Finding

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "tech"
    / "artifacts"
    / "schema"
    / "design-spec.schema.json"
)

_ALLOWED_CONTROL = frozenset("\t\n\r")
_FORBIDDEN_CONTROL = frozenset(
    chr(code) for code in range(0x20) if chr(code) not in _ALLOWED_CONTROL
) | {chr(0x7F)}


def load_schema(path: Path | None = None) -> Mapping[str, Any]:
    return json.loads((path or SCHEMA_PATH).read_text(encoding="utf-8"))


def _child(path: str, key: str) -> str:
    return f"{path}/{key}" if path else f"/{key}"


def _check_string(schema: Mapping[str, Any], value: Any, path: str) -> list[Finding]:
    if not isinstance(value, str):
        return [Finding(Code.INVALID_VALUE, path, "必须是文本")]
    normalized = value.strip()
    findings: list[Finding] = []
    minimum = int(schema.get("minLength", 0))
    maximum = int(schema.get("maxLength", 0)) or None
    if len(normalized) < minimum:
        code = Code.SPEC_REQUIRED if minimum >= 1 else Code.INVALID_VALUE
        findings.append(Finding(code, path, "不能为空" if minimum >= 1 else "文本过短"))
        return findings
    if any(character in _FORBIDDEN_CONTROL for character in normalized):
        findings.append(Finding(Code.INVALID_VALUE, path, "不能包含控制字符"))
    if maximum is not None and len(normalized) > maximum:
        findings.append(
            Finding(Code.INVALID_VALUE, path, f"不超过 {maximum} 个字符，当前 {len(normalized)}")
        )
    pattern = schema.get("pattern")
    if pattern:
        if re.match(str(pattern), normalized) is None:
            findings.append(
                Finding(
                    Code.INVALID_VALUE,
                    path,
                    "需以字母开头，仅含字母、数字、下划线或连字符",
                )
            )
    return findings


def _check(
    schema: Mapping[str, Any],
    value: Any,
    path: str,
    root: Mapping[str, Any],
) -> list[Finding]:
    reference = schema.get("$ref")
    if reference is not None:
        name = str(reference)
        if not name.startswith("#/$defs/"):
            return [Finding(Code.INVALID_VALUE, path or "/", "无法解析契约类型引用")]
        target = root.get("$defs", {}).get(name[len("#/$defs/") :])
        if target is None:
            return [Finding(Code.INVALID_VALUE, path or "/", "无法解析契约类型引用")]
        return _check(target, value, path, root)

    if "const" in schema and value != schema["const"]:
        return [Finding(Code.INVALID_VALUE, path or "/", f"必须为 {schema['const']!r}")]

    allowed = schema.get("enum")
    if allowed is not None and value not in allowed:
        options = "、".join(str(item) for item in allowed)
        return [Finding(Code.INVALID_VALUE, path or "/", f"可选值为 {options}")]

    kind = schema.get("type")
    if kind == "string":
        return _check_string(schema, value, path or "/")
    if kind == "object":
        if not isinstance(value, Mapping):
            return [Finding(Code.INVALID_VALUE, path or "/", "必须是对象")]
        findings = [
            Finding(Code.SPEC_REQUIRED, _child(path, key), "缺少必填字段")
            for key in schema.get("required", ())
            if key not in value
        ]
        properties = schema.get("properties", {})
        for key, item in value.items():
            if not isinstance(key, str):
                findings.append(Finding(Code.INVALID_VALUE, path or "/", "字段名必须是文本"))
                continue
            if key in properties:
                findings.extend(_check(properties[key], item, _child(path, key), root))
            elif schema.get("additionalProperties") is False:
                findings.append(Finding(Code.UNKNOWN_FIELD, _child(path, key), "不支持此字段"))
        return findings
    if kind == "array":
        if not isinstance(value, list):
            return [Finding(Code.INVALID_VALUE, path or "/", "必须是数组")]
        findings = []
        minimum = int(schema.get("minItems", 0))
        maximum = int(schema.get("maxItems", 0)) or None
        if len(value) < minimum:
            findings.append(Finding(Code.SPEC_REQUIRED, path or "/", f"至少需要 {minimum} 项"))
        if maximum is not None and len(value) > maximum:
            findings.append(
                Finding(Code.INVALID_VALUE, path or "/", f"最多支持 {maximum} 项，当前 {len(value)}")
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                findings.extend(_check(item_schema, item, f"{path}/{index}", root))
        return findings
    return []


def check_content(
    content: Any,
    *,
    schema: Mapping[str, Any] | None = None,
) -> tuple[Finding, ...]:
    """按契约 Schema 校验一份 DesignSpecContent。返回空元组表示结构通过。"""

    contract = schema or load_schema()
    findings = _check(contract, content, "", contract)

    if isinstance(content, Mapping):
        requirements = content.get("requirements")
        if isinstance(requirements, list):
            seen: dict[str, int] = {}
            for index, requirement in enumerate(requirements):
                if not isinstance(requirement, Mapping):
                    continue
                identifier = requirement.get("id")
                if not isinstance(identifier, str):
                    continue
                key = identifier.strip()
                if key in seen:
                    findings.append(
                        Finding(
                            Code.DUPLICATE_ID,
                            f"/requirements/{index}/id",
                            f"与 /requirements/{seen[key]}/id 重复：{key}",
                            requirement_id=key,
                        )
                    )
                else:
                    seen[key] = index
    return tuple(findings)


def normalized_requirement_ids(content: Mapping[str, Any]) -> tuple[str, ...]:
    """按出现顺序返回 trim 后的需求 ID，供追踪映射复用。"""

    requirements = content.get("requirements")
    if not isinstance(requirements, list):
        return ()
    return tuple(
        str(item["id"]).strip()
        for item in requirements
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    )


__all__ = [
    "SCHEMA_PATH",
    "check_content",
    "load_schema",
    "normalized_requirement_ids",
]
