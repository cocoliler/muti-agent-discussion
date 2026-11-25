# 规格校验与 FlowStudio 交接

成员 5 的交付实现：把记录员的候选稿转成 FlowStudio v3.1 的 `DesignSpecContent` v1.0，负责
需求 ID、来源覆盖、验收条件与签收校验，并交付 JSON 导出和规格版本 API 适配。

本目录只回答三个问题：**这份草稿能不能签收**、**签收后的 JSON 能不能被 FlowStudio 导入**、
**怎么把正文写进 FlowStudio 而不产生重复版本**。它不调用模型、不访问数据库、不渲染界面。

## 1. 目录

```text
spec_handoff/
├── codes.py       # 缺陷码（Code）、接口错误码（ApiCode）、Finding
├── schema.py      # 按 tech/artifacts/schema/design-spec.schema.json 做结构校验
├── draft.py       # 草稿契约 DraftSpec 与 DesignSpecContent 转换
├── validation.py  # 交付校验：结构 / 来源追溯 / 验收条件 / 需求 ID 稳定
├── signoff.py     # 签收门禁与 SpecHandoff 记录
├── export.py      # UTF-8 JSON 导出、字段顺序、规范化散列、1 MiB 上限
├── handoff.py     # POST /projects/{id}/spec-revisions 适配器（幂等、重试、版本冲突）
├── cli.py         # python -m spec_handoff check|export
├── samples/       # 已验证可导入的示例正文
└── tests/         # 单元测试（标准库 unittest，无第三方依赖）
```

## 2. 交付流水线

```mermaid
flowchart LR
    D[记录员候选稿 draft_content] --> P[DraftSpec.parse]
    P --> C[to_content: DesignSpecContent]
    C --> V[结构校验 + 验收条件]
    P --> T[来源追溯 + ID 稳定]
    V --> G{签收门禁}
    T --> G
    G -->|有 error| X[退回修订，返回缺陷码与 JSON Pointer]
    G -->|通过| H[SpecHandoff: 正文 + review_id + content_hash]
    H --> E[UTF-8 JSON 导出]
    H --> A[POST /api/v1/projects/{id}/spec-revisions]
```

签收结论本身不推进会话状态：阶段迁移仍由 `discussion_orchestration.DiscussionEngine.confirm`
负责，本包只给「能不能签收」的确定性结论（与设计文档 3.2 的门禁一一对应）。

## 3. 草稿契约

记录员提交的 `draft_content` 固定为下列结构。除 12 个 `DesignSpecContent` 字段外，
另有 5 个**只用于校验、不导出**的追溯字段：

| 字段 | 作用 |
| --- | --- |
| `requirements[].source_seqs` | 该需求依据的已提交消息序号；必须是本会话真实存在的序号 |
| `requirements[].covers` | 该需求覆盖的痛点 / 风险 ID |
| `pain_points[]` | `{id, text, confirmed}`；`confirmed=true` 的痛点必须有需求覆盖 |
| `risks[]` | `{id, text, must_handle}`；须处理的风险必须有需求覆盖，且覆盖它的需求要有可观察验收条件 |
| `removed_requirement_ids[]` | 相对上一版显式删除的需求 ID；未登记视为 ID 漂移 |

```json
{
  "product_name": "内部会议室预约",
  "background": "员工通过聊天工具登记会议室，容易重复占用。",
  "goals": ["员工可查询空闲时段并完成预约"],
  "target_users": ["发起会议的员工"],
  "requirements": [{
    "id": "FR-02",
    "kind": "functional",
    "priority": "must",
    "text": "同一会议室的重叠时段最多只有一笔有效占用。",
    "acceptance_criteria": ["两个并发请求只有一个成功，另一个收到冲突提示"],
    "source_seqs": [2, 3],
    "covers": ["R-1"]
  }],
  "technical_constraints": ["预约占用结果由服务端保证"],
  "development_constraints": [],
  "delivery_requirements": ["交付预约界面与冲突测试"],
  "acceptance_criteria": ["现场演示并发预约只有一个成功"],
  "supplemental_text": "",
  "confirmed_assumptions": [],
  "pain_points": [{"id": "P-1", "text": "看不到房间占用", "confirmed": true}],
  "risks": [{"id": "R-1", "text": "并发写入可能双重占用", "must_handle": true}],
  "removed_requirement_ids": []
}
```

草稿里的其它键一律忽略，不会进入正文；正文未知字段由结构校验报 `UNKNOWN_FIELD`。

## 4. 校验规则

`build_report(draft, session=..., parent=...)` 汇总全部检查，任一 `error` 即禁止签收与导出。

| 场景 | 缺陷码 | 定位与说明 |
| --- | --- | --- |
| 必填字段缺失 | `SPEC_REQUIRED` | JSON Pointer，如 `/goals` |
| 未知字段 / 枚举非法 / 超长 / 控制字符 / ID 模式不符 | `UNKNOWN_FIELD`、`INVALID_VALUE` | 与 FlowStudio 导入校验同语义 |
| 需求 ID 重复 | `DUPLICATE_ID` | `/requirements/{i}/id` |
| 需求未引用任何发言 | `SOURCE_MISSING` | `/requirements/{i}/source_seqs` |
| 引用了不存在的消息序号 | `SOURCE_UNKNOWN` | 跨会话引用同样被拒 |
| `covers` 指向未知痛点 / 风险 | `UNKNOWN_TRACE_ITEM` | `/requirements/{i}/covers/{j}` |
| 已确认痛点或须处理风险没有需求覆盖 | `REQUIRED_UNCOVERED` | `/pain_points/{i}`、`/risks/{i}` |
| 需求没有验收条件 / 验收条件与风险整改无关 | `ACCEPTANCE_MISSING` | `/requirements/{i}/acceptance_criteria` |
| 同一需求内验收条件重复 | `ACCEPTANCE_DUPLICATE` | 逐条定位 |
| 上一版需求消失且未登记 | `ID_DROPPED` | 需求 ID 跨修订稳定，删除须写入 `removed_requirement_ids` |

## 5. 签收门禁

`sign_off(session, draft_id=..., input_version=...)` 按下列顺序判定，命中即返回：

1. 会话处于 `AWAITING_CONFIRMATION`，否则 `INVALID_STATUS`；
2. 草稿仍是当前版本，否则 `STALE_DRAFT`；
3. 输入版本与当前一致，否则 `STALE_INPUT_VERSION`；
4. 存在同版本 `PASS` 评审且 `draft.review_id` 与之匹配，否则 `REVIEW_NOT_PASSED`；
5. 通过评审中无 `blocking` 发现，否则 `BLOCKING_FINDING`；
6. 会话无开放阻断问题，否则 `OPEN_BLOCKING_ISSUE`；
7. 成稿条件（必需发言、质疑、阻断问题）齐备，否则 `NOT_SIGNED_OFF`；
8. 第 3–4 节的交付校验全通过。

通过后生成 `SpecHandoff`（`session_id, project_id, draft_id, draft_version, review_id,
input_version, content, content_hash, confirmed_at`），对应设计文档第 5 节的 `SpecHandoff` 记录。

## 6. 导出

- 根节点就是 `DesignSpecContent`，没有外层包装，没有工作台元数据；
- UTF-8 无 BOM、2 空格缩进、LF 行尾、字段顺序按契约 `required` 排列；
- 文件上限 1 MiB（`EXPORT_TOO_LARGE`），与前端导入、后端请求体上限一致；
- `content_hash` 与 FlowStudio `content_hash` 同一规范化算法（键排序、紧凑分隔符、
  `ensure_ascii=False`），可直接比对 `SpecRevision.hash`；
- 需要归档评审与草稿关联时用 `export_handoff_bundle`，该包**不可**用于导入；
- 讨论工作台的 `GET /api/v1/discussions/{id}/export`（可带 `?draft_id=` 导出任意草稿版本）与
  `/api/demo/{id}/export` 直接复用 `dump_text` + `check_export`，下载到的文件就是本节的正文，
  可直接导入 FlowStudio。

## 7. 规格版本 API 交接

`SpecRevisionHandoff(base_url)` 提交 `POST /api/v1/projects/{project_id}/spec-revisions`：

| 行为 | 实现 |
| --- | --- |
| 请求体 | `{expected_revision, content}`，与 `SpecRevisionWriteRequest` 同形；首次为 `0` |
| 幂等 | 写命令带 `Idempotency-Key`（UUID），同键同体返回原结果 |
| 可重试错误 | `STORAGE_BUSY`、网络错误、5xx：同键重发，退避 1s / 3s，默认 3 次 |
| 版本冲突 | 409 `REVISION_CONFLICT` → 记录置 `CONFLICT`；`latest_revision()` 读取当前版本后 `rebase()` 换新键重投（请求体已变，必须换键） |
| 幂等冲突 | 409 `IDEMPOTENCY_CONFLICT` → 置 `FAILED`，不重试 |
| 校验失败 | 422 `VALIDATION_FAILED` / 413 `PAYLOAD_TOO_LARGE` → 置 `FAILED`，透传服务端信息 |
| 正文被改 | 提交前比对 `content_hash`，不一致则本地拒绝，不占用幂等键 |

`SubmissionRecord.as_dict()` 携带正文、目标版本、幂等键与每次尝试，供后端持久化待提交记录；
响应丢失时按同键重发即可，不会生成第二个规格版本。

## 8. 命令行

```powershell
# 校验一份已定稿正文（结构 + 1 MiB）
python -m spec_handoff check spec_handoff/samples/campus-meeting.spec.json

# 从草稿转换并导出；--seqs 提供追溯用的已提交消息序号
python -m spec_handoff export draft.json -o campus-meeting.spec.json --mode draft --seqs 1,2,3

# 按内容自动判断草稿 / 正文
python -m spec_handoff export input.json -o output.json
```

`check` 与 `export` 在存在 `error` 时返回退出码 1；`export` 需要 `--force` 才会写出不可导入的结果。

## 9. 示例文件

`spec_handoff/samples/campus-meeting.spec.json` 由用户提供的
`campus-meeting-spec.json`（跨园区会议室预约与访客协同平台，R01–R08）经 `check` + `export`
生成，已通过：

- 本仓库结构与导出校验：`ok=true`，6459 字节；
- FlowStudio 契约模型：`DesignSpecContent.model_validate` 与
  `SpecRevisionWriteRequest.model_validate` 均通过；
- 散列一致：`content_hash` 与 FlowStudio `content_hash()` 相同，
  `66912432ab4f35b338e2b8c06a582722bd920a796879974195206f3ceae60691`。

该文件可直接用 FlowStudio 现有「导入 JSON」入口加载，需求 ID 与验收条件保持原样。

## 10. 测试

```powershell
python -m unittest discover -s spec_handoff/tests -t . -v
```

覆盖：草稿转换不泄漏追溯字段、必填与枚举校验、ID 唯一与跨版本稳定、来源覆盖、风险整改
验收条件、签收门禁的 8 个失败分支、导出格式与 1 MiB 上限、API 适配的成功/重试/版本冲突/
幂等冲突/正文篡改，以及示例文件的导入回归。

## 11. 与其它成员的接口

| 依赖 | 本包用法 | 现状 |
| --- | --- | --- |
| 成员 1 讨论编排 | 只读 `DiscussionSession.current_draft / reviews / issues / messages` 与 `draft_blockers` | 已在 `discussion_orchestration/` 交付，可直接运行 |
| 成员 2 角色执行 | 记录员 `draft_content` 按第 3 节契约输出 | 契约已固定，本包负责转换与报错定位 |
| 成员 3 后端持久化 | 存 `SubmissionRecord.as_dict()` 与 `SpecHandoff`，提供 `POST /spec-revisions` 代理 | 接口语义已按 FlowStudio 契约对齐 |
| 成员 4 前端 | 展示 `Finding.code + path`，签收按钮调用 `sign_off` 结果 | 缺陷码稳定、可直接映射提示 |
| 成员 6 集成测试 | `samples/` 与 `tests/` 作为验收入口 | 端到端流程仍需成员 6 串起 |

本包不实现：角色提示词与模型调用、数据库与 HTTP 服务、群聊与签收界面、FlowStudio 侧代码。
