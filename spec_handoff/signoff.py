"""签收门禁与 `SpecHandoff` 记录。

设计文档 3.2 规定签收必须同时满足：草稿仍是当前版本、同版本评审通过、没有开放阻断项、
结构校验通过。未通过评审或仍有阻断项时不得签收，也不得导出可导入正文。

本模块只读讨论会话（`DiscussionSession`），不改状态：把「能不能签收」变成可测试的结论，
会话阶段迁移仍由 `DiscussionEngine.confirm` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from discussion_orchestration import (
    DiscussionSession,
    Severity as ReviewSeverity,
    SessionStatus,
    Verdict,
    draft_blockers,
)

from .codes import Code, Finding
from .draft import DraftSpec
from .export import content_hash
from .validation import DeliveryReport, build_report, parent_draft


@dataclass(frozen=True)
class SpecHandoff:
    """签收交接记录，对应设计文档第 5 节的 `SpecHandoff`。

    `content` 即签收的 `DesignSpecContent` 正文；`content_hash` 与 FlowStudio
    `SpecRevision.hash` 同一算法，可用于比对服务端实际保存的内容。
    """

    session_id: str
    project_id: str
    draft_id: str
    draft_version: int
    review_id: str
    input_version: int
    content: Mapping[str, Any]
    content_hash: str
    confirmed_at: str
    open_findings: tuple[Finding, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "draft_id": self.draft_id,
            "draft_version": self.draft_version,
            "review_id": self.review_id,
            "input_version": self.input_version,
            "content_hash": self.content_hash,
            "confirmed_at": self.confirmed_at,
            "open_findings": [item.as_dict() for item in self.open_findings],
        }


@dataclass(frozen=True)
class SignOffResult:
    """签收结论。`ok=False` 时 `handoff` 为空，`findings` 给出可诊断原因。"""

    ok: bool
    findings: tuple[Finding, ...]
    report: DeliveryReport | None = None
    handoff: SpecHandoff | None = None

    @property
    def content(self) -> Mapping[str, Any] | None:
        return self.handoff.content if self.handoff else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [item.as_dict() for item in self.findings],
            "handoff": self.handoff.as_dict() if self.handoff else None,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sign_off(
    session: DiscussionSession,
    *,
    draft_id: str,
    input_version: int,
    now: str | None = None,
) -> SignOffResult:
    """执行签收门禁；通过时生成交接记录。"""

    findings: list[Finding] = []

    if session.status is not SessionStatus.AWAITING_CONFIRMATION:
        findings.append(
            Finding(
                Code.INVALID_STATUS,
                "/status",
                f"当前阶段为 {session.status.value}，只有等待用户确认时才能签收",
            )
        )
        return SignOffResult(ok=False, findings=tuple(findings))

    draft = session.current_draft
    if draft is None or draft.draft_id != draft_id:
        findings.append(Finding(Code.STALE_DRAFT, "/draft_id", "签收的草稿不是当前版本"))
        return SignOffResult(ok=False, findings=tuple(findings))

    if input_version != session.input_version or draft.input_version != session.input_version:
        findings.append(
            Finding(
                Code.STALE_INPUT_VERSION,
                "/input_version",
                f"签收的输入版本已过期，当前为 v{session.input_version}",
            )
        )
        return SignOffResult(ok=False, findings=tuple(findings))

    review = session.review_of(draft.draft_id)
    if review is None or review.verdict is not Verdict.PASS or draft.review_id != review.review_id:
        findings.append(
            Finding(
                Code.REVIEW_NOT_PASSED,
                "/review_id",
                "当前草稿没有同版本的通过评审",
            )
        )
    else:
        blocking_findings = [
            item for item in review.findings if item.severity is ReviewSeverity.BLOCKING
        ]
        for position, item in enumerate(blocking_findings):
            findings.append(
                Finding(
                    Code.BLOCKING_FINDING,
                    f"/review/findings/{position}",
                    f"{item.finding_id}：{item.summary}",
                )
            )

    for issue_id in (issue.issue_id for issue in session.blocking_issues()):
        findings.append(
            Finding(Code.OPEN_BLOCKING_ISSUE, "/issues", f"阻断问题 {issue_id} 仍未关闭")
        )
    for code in draft_blockers(session):
        findings.append(
            Finding(
                Code.NOT_SIGNED_OFF,
                "/draft",
                f"成稿条件未满足：{code.value}",
            )
        )

    spec = DraftSpec.parse(draft.content)
    report = build_report(spec, session=session, parent=parent_draft(session.drafts, draft))
    findings.extend(report.findings)
    if findings:
        return SignOffResult(ok=False, findings=tuple(findings), report=report)

    handoff = SpecHandoff(
        session_id=session.session_id,
        project_id=session.project_id,
        draft_id=draft.draft_id,
        draft_version=draft.version,
        review_id=review.review_id if review else "",
        input_version=session.input_version,
        content=report.content,
        content_hash=content_hash(report.content),
        confirmed_at=now or _now(),
        open_findings=report.warnings,
    )
    return SignOffResult(ok=True, findings=(), report=report, handoff=handoff)


def export_handoff(handoff: SpecHandoff) -> str:
    """把交接记录导出为 UTF-8 JSON 文本。

    默认只导出正文——FlowStudio 导入入口只接受 `DesignSpecContent`。
    需要连带版本与评审元数据时使用 `export_handoff_bundle`。
    """

    from .export import dump_text

    return dump_text(handoff.content)


def export_handoff_bundle(handoff: SpecHandoff) -> str:
    """导出「正文 + 交接元数据」包，供工作台归档或人工比对，不用于 FlowStudio 导入。"""

    import json

    payload = {
        "schema_version": "1.0",
        "handoff": handoff.as_dict(),
        "spec_content": dict(handoff.content),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


__all__ = [
    "SignOffResult",
    "SpecHandoff",
    "export_handoff",
    "export_handoff_bundle",
    "sign_off",
]
