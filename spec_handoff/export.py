"""UTF-8 JSON 导出。

FlowStudio 现有导入入口的要求（前端 `storage.readJson` + `plan.validateSpec`）：

- 单文件不超过 1 MiB，UTF-8 编码，不允许重复 JSON key；
- 文档根节点就是 `DesignSpecContent`，未知字段直接拒绝；
- 需求 ID 唯一、必填字段齐全（由 `schema.check_content` 保证）。

因此导出只写正文、固定字段顺序、不做任何包装；`content_hash` 与 FlowStudio
`content_hash` 同一规范化算法（键排序、紧凑分隔符、`ensure_ascii=False`），
可直接与服务端返回的 `SpecRevision.hash` 比对。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .codes import Code, Finding
from .draft import CONTENT_FIELDS, REQUIREMENT_FIELDS
from .schema import check_content

#: 与契约模块 `MAX_BODY_BYTES` 一致；前端导入同样限制 1 MiB。
MAX_EXPORT_BYTES = 1_048_576


def order_content(content: Mapping[str, Any]) -> dict[str, Any]:
    """按契约字段顺序重排正文，未知字段保持原位附加在末尾（结构校验会另行报错）。"""

    ordered: dict[str, Any] = {}
    for key in CONTENT_FIELDS:
        if key in content:
            ordered[key] = content[key]
    for key, value in content.items():
        if key not in ordered:
            ordered[key] = value

    requirements = ordered.get("requirements")
    if isinstance(requirements, list):
        ordered["requirements"] = [
            _order_requirement(item) if isinstance(item, Mapping) else item
            for item in requirements
        ]
    return ordered


def _order_requirement(requirement: Mapping[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for key in REQUIREMENT_FIELDS:
        if key in requirement:
            ordered[key] = requirement[key]
    for key, value in requirement.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def dump_text(content: Mapping[str, Any]) -> str:
    """生成可直接写入磁盘的 UTF-8 文本（缩进 2，保留中文，行尾 LF）。"""

    return json.dumps(order_content(content), ensure_ascii=False, indent=2) + "\n"


def dump_bytes(content: Mapping[str, Any]) -> bytes:
    return dump_text(content).encode("utf-8")


def content_hash(content: Mapping[str, Any]) -> str:
    """规范化 SHA-256，与 FlowStudio `content_hash` 一致。"""

    canonical = json.dumps(
        order_content(content),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_export(content: Any) -> tuple[Finding, ...]:
    """导出前检查：结构合法且不超过 1 MiB。"""

    findings = list(check_content(content))
    if isinstance(content, Mapping):
        size = len(dump_bytes(content))
        if size > MAX_EXPORT_BYTES:
            findings.append(
                Finding(
                    Code.EXPORT_TOO_LARGE,
                    "/",
                    f"导出文件 {size} 字节，超过 {MAX_EXPORT_BYTES} 字节上限",
                )
            )
    return tuple(findings)


def write_export(path: str | Path, content: Mapping[str, Any]) -> Path:
    """写入 UTF-8 JSON 文件（不带 BOM），返回写入路径。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_text(content), encoding="utf-8", newline="\n")
    return target


__all__ = [
    "MAX_EXPORT_BYTES",
    "check_export",
    "content_hash",
    "dump_bytes",
    "dump_text",
    "order_content",
    "write_export",
]
