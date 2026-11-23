# 讨论工作台后端（API 与持久化）

讨论工作台的 HTTP 接口与 SQLite 持久化层。编排决策在仓库根目录 `discussion_orchestration/`（成员 1），模型调用在 `backend/roles/`（成员 2）；本目录只负责**把引擎状态存下来、按 `seq` 读回来、按契约暴露出去**。

依据：`README.md` 分工表、`FLOWSTUDIO_DESIGN_INPUT.md` §5/§6、`architecture/API_SPECIFICATION.md`。

依赖：Python 3.11+、`fastapi`、`pydantic>=2`；签收门禁调用仓库内 `spec_handoff/`（成员 5）；存储用标准库 `sqlite3`，无额外驱动。

## 目录

```text
backend/discussions/
├── codec.py        # 引擎 dataclass <-> JSON（唯一反射引擎模型的文件）
├── schema.sql      # 8 张表，幂等建表 + 已发布库的列迁移
├── repository.py   # 事务、seq 镜像、CAS、幂等、调用尝试、草稿/评审/交接
├── schemas.py      # HTTP 出入参（契约定稿只需改这里）
├── errors.py       # ReasonCode -> HTTP 映射与错误信封
├── application.py  # 协调服务：幂等 -> 读状态 -> 调引擎 -> 单事务落库
└── routes.py       # /api/v1/discussions/*，含 install(app)
```

## 接线

```python
from backend.discussions.routes import install

install(app)                    # 只追加路由与异常处理器，既有 demo 路由原样保留
install(app, with_export=True)  # 仅在导出操作登记进契约后再开启
```

存储路径由环境变量 `DISCUSSION_DB` 决定，默认 `backend/.data/discussions.sqlite3`。首次连接自动建表；启动时把上次未终结的调用置为 `FAILED` / `PROCESS_INTERRUPTED`，避免前端一直看到「运行中」。

## 接口

严格 6 条，与 `FLOWSTUDIO_DESIGN_INPUT.md` §6 一一对应；契约之外不注册任何路由。

| 方法 | 路径 | 成功码 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/projects/{project_id}/discussions` | 201 | 创建会话；幂等键必填 |
| GET | `/api/v1/discussions/{id}` | 200 | 阶段、参与者、当前草稿与正文、草稿/评审历史、预算、`state_version` |
| GET | `/api/v1/discussions/{id}/messages?after_seq=&limit=` | 200 | 增量分页；`limit` 默认 20 / 最大 100，游标 `next_cursor` |
| POST | `/api/v1/discussions/{id}/messages` | 200 | 用户补充/回复，带上下文 `input_version` |
| POST | `/api/v1/discussions/{id}/actions` | 200 | `pause`/`resume`/`cancel`/`extend_quota`/`require_changes`/`resolve_issues`/`submit_*` |
| POST | `/api/v1/discussions/{id}/confirm` | 200 | 签收：先过 `spec_handoff.sign_off` 门禁，通过后写 `spec_handoffs`（含正文与 `content_hash`） |

所有写命令要求 `Idempotency-Key`（UUID），scope 为「HTTP 方法 + 资源路径」：同键同体回放原响应，同键异体返回 409 `IDEMPOTENCY_CONFLICT`，入参校验失败不占用键。

## 数据表

`discussion_sessions`（整包 `session_state` + `state_version`）、`discussion_messages`（`PRIMARY KEY(session_id, seq)`）、`role_attempts`、`draft_revisions`、`review_results`、`spec_handoffs`（含签收正文 `content_json` 与 `content_hash`）、`idempotency_keys`、`discussion_events`。

## 三条不可违背的实现约定

1. **`seq` 由引擎生成，数据库只镜像。** SQL 里不出现 `seq + 1`；投影序号不连续直接抛 `DuplicateProjection`，而不是静默写入。
2. **`session_state` 整包为唯一事实源。** 消息表是它的投影，只为 `after_seq` 分页、引用校验与历史查看服务；重启后从整包恢复，点名状态与轮次计数不丢。草稿与评审在每次写事务内投影到 `draft_revisions` / `review_results`。
3. **编排拒绝不是 HTTP 错误。** `decision.ok is False` 返回 200 + decision 信封（前端据此渲染阻塞原因与可用操作）；只有入参非法、版本冲突、幂等冲突、存储繁忙才走 4xx/5xx。

签收门禁由 `spec_handoff.sign_off` 判定（纯读、不改状态），拒绝时同样返回 200 + `decision.ok=false`，`result.findings` 给出 `code/path/message`，前端可定位到具体条目；**门禁在引擎推进状态之前执行**，所以被拒绝的请求不会留下半签收的会话。

## 错误码

404 `NOT_FOUND`、409 `REVISION_CONFLICT` / `IDEMPOTENCY_CONFLICT`、413 `PAYLOAD_TOO_LARGE`（1 MiB，ASGI 层拦截）、422 `VALIDATION_FAILED`、503 `STORAGE_BUSY`（SQLite 写锁超时）。

信封固定为 `{"error": {code, message, issues[], request_id}}`，`request_id` 缺省时自动生成。

## 待办

- 6 个接口尚未登记进 FlowStudio 契约生成流程（`tech/flowstudio_contracts`，位于 SE-lib）；`tech/artifacts/openapi.json` 目前仍不含 `/discussions`。
- 签收后的规格保存由成员 5 的 `SpecRevisionHandoff` 负责（`POST /projects/{id}/spec-revisions`，`expected_revision` CAS + `Idempotency-Key`）；本层负责把返回的 `spec_revision_id` 回填到 `spec_handoffs`。
- 成员 2 的 runner 需调用 `record_attempt` / `finish_attempt`，才能让 `GET /discussions/{id}` 的参与者状态与预算消耗反映真实调用。
