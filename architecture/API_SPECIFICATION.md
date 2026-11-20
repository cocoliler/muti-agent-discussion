# REST API 契约
## 通用协议
前缀 /api/v1，采用 UTF-8 JSON，最大请求 1 MiB。请求与响应模型直接来自 [契约模块](https://github.com/cocoliler/SE-lib/tree/699d136b93e46f6d92cc501f03718dee766127bc/tech/flowstudio_contracts)，操作的唯一事实源是 `flowstudio_contracts.api.OPERATIONS`；[生成的 OpenAPI](../tech/artifacts/openapi.json) 由该表产出，后端不再单独导出 openapi.json。`backend/tools/export_openapi.py` 将 FastAPI 实际路由与操作表比对（方法、路径、成功状态码、响应模型名），CI/本地均可执行。下表用于阅读接口，精确参数及响应以生成产物为准。
UUID 路径必须合法，版本整数≥1；分页 limit 默认20/max100，cursor 不透明；未知字段422。
错误体 {error:{code,message,issues:Issue[],request_id}}；404 NOT_FOUND，409 REVISION_CONFLICT/IDEMPOTENCY_CONFLICT，413 PAYLOAD_TOO_LARGE，422 VALIDATION_FAILED，429 QUEUE_FULL，503 STORAGE_BUSY。`error.code` 取自契约 `ErrorCode`，`issues[].code` 取自 `IssueCode`；后者集合更窄，因此如 REVISION_CONFLICT / RESULT_NOT_READY 只出现在信封层，`issues` 允许为空数组。
异步模型错误写 PlanningRun.error，查询仍200；不存在记录才404。客户端不得把查询200当规划成功。
写命令携 Idempotency-Key（UUID 字符串）。同 scope/key 与规范化 body hash 返回原结果；同键异内容409。校验失败不占用键；提交后断线可同键重试。
scope 为 HTTP 方法+资源路径。GET 不产生新版本。无用户鉴权系统，loopback+Origin/Host 检查，不作为公网服务。

## 资源接口
| 方法与路径 | 请求 | 成功响应 / 失败条件 |
|---|---|---|
| POST /projects | {name:string1..120} | 201 Project |
| GET /projects | ?limit&cursor | 200 {items:Project[],next_cursor:null/string} |
| POST /projects/{id}/spec-revisions | {expected_revision:int≥0,content:DesignSpecContent} | 201 SpecRevision；初次expected=0，冲突409 |
| GET /projects/{id}/spec-revisions | 分页 | 200 {items:SpecRevision[],next_cursor} |
| GET /spec-revisions/{id} | 无 | 200 SpecRevision |
| POST /spec-validations | {content:DesignSpecContent} | 200 ValidationReport；不能解析请求则422 |
| POST /planning-runs | {spec_revision_id,mode,config:{granularity,model,planning_constraints,policy?},fixture_id?} | 202 {id,status:"QUEUED",poll_url}；TEST必须fixture，REAL禁止fixture |
| GET /planning-runs/{id} | 无 | 200 PlanningRun，proposal/report 可空，终态附结果或error |
| GET /planning-runs/{id}/tasks | 无 | 200 {tasks:Task[]}；未SUCCEEDED返回409 RESULT_NOT_READY |
| POST /orchestrations | {spec_revision_id,policy?,tasks:Task[]} | 200 {input_hash,edges,execution_stages,parallel_batches,ordered_task_ids,validation_report}；无效仍200 report.valid=false，派生结果空 |
| POST /workflow-validations | 同上 | 200 ValidationReport（复用同一build，不复制规则） |
| POST /projects/{id}/plans | {spec_revision_id,planning_run_id?,policy?,tasks,confirmed_assumption_ids:[],layout?} | 201 {plan_id,revision:PlanRevision}；来源服务端推导，重算后不合法422 |
| POST /plans/{id}/revisions | 上述字段+{expected_revision,parent_revision_id} | 201 PlanRevision；409冲突/422不合法 |
| GET /plans/{id}/revisions | 分页 | 200 {items:PlanRevision摘要[],next_cursor} |
| GET /plan-revisions/{id} | 无 | 200 PlanRevision，包含Task与派生结果 |
| GET /plan-revisions/{id}/layout | 无 | 200 Layout，缺省layout revision=0 |
| PUT /plan-revisions/{id}/layout | {expected_revision,positions,notes:CanvasNote[],viewport} | 200 Layout，独立CAS；坐标有限数；positions 的 key 必须是该版本内已存在的 task_id，notes 是画布批注（id/text/x/y/color?），不绑定任务 |
| GET /plan-revisions/{id}/export | ?format=json | 200 ExportBundle，Content-Disposition attachment；含Schema版本、来源与规格/规划约束快照 |

导出 ExportBundle={schema_version:"1.0",spec_snapshot,policy_snapshot,plan_revision,layout}。
P0 导入规格 JSON 和导出计划 JSON；完整计划包的再次导入为 P1，走校验并重建全局 ID，不能直接信任包内派生字段。

## 请求示例与回放
以下 UUID 为示例，实际使用前一步返回值。规格 body 由用户输入并符合 DesignSpecContent Schema。
```json
{"spec_revision_id":"11111111-1111-4111-8111-111111111111","mode":"REAL","config":{"granularity":"standard","model":"configured-default","planning_constraints":[]}}
```
model="configured-default" 是本系统解析的配置别名，服务端解析并持久化实际模型名，不直接发给供应商。
```json
{"id":"22222222-2222-4222-8222-222222222222","status":"QUEUED","poll_url":"/api/v1/planning-runs/22222222-2222-4222-8222-222222222222"}
```
终态 SUCCEEDED：响应含 proposal（TaskProposal）、validation_report、mode、spec_revision_id、config_snapshot、attempt_count、usage。NEEDS_INPUT 的 proposal 含 blocking_questions；FAILED 的 error 包含 code/message，可返回部分响应仅供诊断，不能保存为正式计划。
图校验示例：
```json
{"valid":false,"errors":[{"code":"MISSING_DEPENDENCY","severity":"error","path":"/tasks/1/dependencies/0","message":"任务 B 引用了不存在的 X","task_ids":["B"],"edges":[{"source":"X","target":"B"}],"suggestion":"修正引用或补充任务 X"}],"warnings":[],"coverage":{"covered_ids":[],"uncovered_ids":["FR-01"]},"rules_version":"1.0","input_hash":"0000000000000000000000000000000000000000000000000000000000000000"}
```
input_hash示例为64个0，真实响应使用规范化输入的SHA-256。
保存请求只提交任务与来源，不提交 validity/batches/source。服务端比对 PlanningRun.proposal：相同且 mode=REAL→AI，相同TEST→TEST，发生人工变化→MANUAL，同时保留 origin_mode 和 planning_run_id，不能洗掉 TEST 来源。
网络失败先以同幂等键重发；409 展示最新版本及本地差异；422 保留草稿并定位。
SpecRevision、Task 和派生字段以 [领域模型](https://github.com/cocoliler/SE-lib/blob/699d136b93e46f6d92cc501f03718dee766127bc/docs/architecture/WORKFLOW_DATA_MODEL.md) 为唯一结构定义，Pydantic 实现位于 `tech/flowstudio_contracts`，OpenAPI/TS 由 T01 生成。后端不维护第二套 DTO，也不提交自己的 openapi.json。

## 实现状态（E：T02/T07）
- 18 个操作均已接入 FastAPI 路由，前缀 `/api/v1`；存储为 SQLite + Alembic，启动时执行迁移、持有实例锁并恢复未终结规划（置 FAILED / PROCESS_INTERRUPTED）。
- 契约之外不新增路由：旧版的 `/health` 已移除，就绪可用 `GET /projects` 判断；契约升级后由漂移检查阻止私加接口。
- 保存与编排复用 T05 `build_plan` / `validate_workflow`；服务端不复制图算法，也不采信客户端提交的 validity/batches/source。
- 请求体结构错误（字段缺失、枚举非法、重复需求 ID、tasks 为空等）在解析阶段返回 422 VALIDATION_FAILED；此时不占用幂等键。业务一致性错误（来源不匹配、关键假设、未知 task_id）返回 422 并携带 `Issue`。
- `notes` 已按契约改为 `CanvasNote[]`；`spec_snapshot` 是规格正文（DesignSpecContent），不再携带 SpecRevision 元数据。
- REAL 模式使用配置的 DeepSeek 模型，缺少服务端密钥时返回 `error.code=MODEL_AUTH`；TEST 必须显式提供 fixture_id。

## 保存一致性补充
若携planning_run_id，服务端验证其状态、项目、spec_revision_id及解析后的policy与请求一致；不一致422 SOURCE_MISMATCH。未携时来源MANUAL、origin_mode=NONE。人工修改旧TEST候选仍保留origin_mode=TEST。所有derived字段服务端重算，用户提交的确认ID必须与当前候选low假设集合精确对应，critical假设阻断。
