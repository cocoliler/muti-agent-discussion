"""命令行入口：校验与导出与 FlowStudio 兼容的规格 JSON。

用法::

    python -m spec_handoff check  campus-meeting.json
    python -m spec_handoff export campus-meeting.json -o campus-meeting.spec.json
    python -m spec_handoff export draft.json --mode draft --seqs 1,2,3

`--mode auto`（默认）按内容判断：含 `source_seqs` / `covers` / `pain_points` /
`risks` / `removed_requirement_ids` 视为草稿，走草稿转换与追溯校验；否则视为已定稿正文，
只做结构与导出检查。`--seqs` 提供草稿追溯所需的已提交消息序号。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .codes import Finding
from .draft import DraftSpec
from .export import check_export, content_hash, dump_text, write_export
from .validation import DeliveryReport, build_report

_DRAFT_MARKERS = frozenset({"pain_points", "risks", "removed_requirement_ids"})


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"JSON 包含重复键：{key}")
        seen.add(key)
    return dict(pairs)


def read_json_file(path: str | Path) -> Any:
    """读取 UTF-8 JSON；拒绝重复键，与 FlowStudio 导入入口一致。"""

    text = Path(path).read_text(encoding="utf-8-sig")
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


def looks_like_draft(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if _DRAFT_MARKERS & set(value.keys()):
        return True
    requirements = value.get("requirements")
    if not isinstance(requirements, list):
        return False
    return any(
        isinstance(item, Mapping) and ("source_seqs" in item or "covers" in item)
        for item in requirements
    )


def parse_seqs(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    seqs: list[int] = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            seqs.append(int(chunk))
        except ValueError as error:
            raise SystemExit(f"--seqs 需要逗号分隔的整数序号：{chunk}") from error
    return tuple(seqs)


def _report(value: Any, mode: str, seqs: Sequence[int] | None) -> DeliveryReport:
    if mode == "auto":
        mode = "draft" if looks_like_draft(value) else "content"
    if mode == "draft":
        draft = DraftSpec.parse(value if isinstance(value, Mapping) else None)
        return build_report(draft, known_seqs=seqs)
    content = value if isinstance(value, Mapping) else {}
    return DeliveryReport(content=content, findings=check_export(content))


def _print(report: DeliveryReport) -> None:
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


def _print_findings(findings: Sequence[Finding]) -> None:
    for item in findings:
        print(
            f"[{item.level.value}] {item.code.value} {item.path}：{item.message}",
            file=sys.stderr,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m spec_handoff",
        description="校验并导出 FlowStudio DesignSpecContent v1.0",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="只校验，不写文件")
    check.add_argument("input", help="输入 JSON 文件")
    check.add_argument("--mode", choices=("auto", "draft", "content"), default="auto")
    check.add_argument("--seqs", default=None, help="草稿追溯用的已提交消息序号，例如 1,2,3")

    export = commands.add_parser("export", help="校验并写出可导入正文")
    export.add_argument("input", help="输入 JSON 文件")
    export.add_argument("-o", "--output", default=None, help="输出路径；缺省打印到 stdout")
    export.add_argument("--mode", choices=("auto", "draft", "content"), default="auto")
    export.add_argument("--seqs", default=None, help="草稿追溯用的已提交消息序号，例如 1,2,3")
    export.add_argument(
        "--force", action="store_true", help="存在阻断问题时仍然写出（仅供排查，导出结果不可导入）"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        value = read_json_file(args.input)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"读取失败：{error}", file=sys.stderr)
        return 2

    report = _report(value, args.mode, parse_seqs(args.seqs))
    if args.command == "check":
        _print(report)
        _print_findings(report.findings)
        return 0 if report.ok else 1

    if not report.ok and not args.force:
        _print(report)
        _print_findings(report.errors)
        print("存在阻断问题，未导出。", file=sys.stderr)
        return 1

    text = dump_text(report.content)
    if args.output:
        write_export(args.output, report.content)
        print(f"已写出 {args.output}", file=sys.stderr)
        print(f"content_hash={content_hash(report.content)}", file=sys.stderr)
        print(f"bytes={len(text.encode('utf-8'))}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    _print_findings(report.findings)
    return 0 if report.ok else 1


__all__ = ["looks_like_draft", "main", "parse_seqs", "read_json_file"]
