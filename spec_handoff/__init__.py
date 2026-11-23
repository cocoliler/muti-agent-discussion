"""草稿到 FlowStudio `DesignSpecContent` v1.0 的校验、导出与交接。

用法概览::

    from spec_handoff import DraftSpec, build_report, sign_off, SpecRevisionHandoff

    draft = DraftSpec.parse(session.current_draft.content)
    report = build_report(draft, session=session)         # 结构 + 追溯 + 验收
    result = sign_off(session, draft_id=..., input_version=session.input_version)
    record = SpecRevisionHandoff(base_url).create(result.handoff, expected_revision=0)
    SpecRevisionHandoff(base_url).submit(record)           # POST /api/v1/projects/{id}/spec-revisions

分层边界：

- 本包不调用模型、不启动服务、不写数据库；
- 讨论状态与轮次由 `discussion_orchestration` 决定，本包只读会话快照；
- 持久化与会话接口由后端协调服务承担，本包提供可序列化的交接记录；
- 群聊与签收界面由前端承担，本包提供缺陷码与 JSON Pointer 定位。
"""

from .codes import ApiCode, Code, Finding, Level, blocking, describe, error_body
from .draft import CONTENT_FIELDS, DraftRequirement, DraftSpec, PainPoint, Risk
from .export import (
    MAX_EXPORT_BYTES,
    check_export,
    content_hash,
    dump_bytes,
    dump_text,
    order_content,
    write_export,
)
from .handoff import (
    Attempt,
    HandoffError,
    HttpRequest,
    HttpResponse,
    LatestRevision,
    SpecRevisionHandoff,
    SubmissionRecord,
    SubmissionStatus,
    Transport,
    urllib_transport,
)
from .schema import check_content, load_schema, normalized_requirement_ids
from .signoff import (
    SignOffResult,
    SpecHandoff,
    export_handoff,
    export_handoff_bundle,
    sign_off,
)
from .validation import (
    DeliveryReport,
    build_report,
    check_acceptance,
    check_id_stability,
    check_traceability,
    parent_draft,
)

__all__ = [
    "ApiCode",
    "Attempt",
    "CONTENT_FIELDS",
    "Code",
    "DeliveryReport",
    "DraftRequirement",
    "DraftSpec",
    "Finding",
    "HandoffError",
    "HttpRequest",
    "HttpResponse",
    "LatestRevision",
    "Level",
    "MAX_EXPORT_BYTES",
    "PainPoint",
    "Risk",
    "SignOffResult",
    "SpecHandoff",
    "SpecRevisionHandoff",
    "SubmissionRecord",
    "SubmissionStatus",
    "Transport",
    "blocking",
    "build_report",
    "check_acceptance",
    "check_content",
    "check_export",
    "check_id_stability",
    "check_traceability",
    "content_hash",
    "describe",
    "dump_bytes",
    "dump_text",
    "error_body",
    "export_handoff",
    "export_handoff_bundle",
    "load_schema",
    "normalized_requirement_ids",
    "order_content",
    "parent_draft",
    "sign_off",
    "urllib_transport",
    "write_export",
]
