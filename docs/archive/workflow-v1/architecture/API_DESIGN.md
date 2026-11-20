# HTTP API 与错误契约

基础路径 `/api/v1`，JSON UTF-8，同源本地会话；变更请求带 CSRF token，详见系统架构。以下为待实现契约。成功直接返回资源，列表返回 `{items,nextCursor}`；时间UTC毫秒，ID为UUID。分页默认50、最大100，cursor是不透明的(createdAt,id)游标。搜索q最多100字。

## 路由清单

| 方法与路径 | 请求/响应摘要 | 约束 |
|---|---|---|
| GET /health | `{status,appVersion,schemaVersion}` | STARTING/READY/DEGRADED；不暴露秘密 |
| GET/POST /projects | q,cursor / `{name,description}` → Project | 创建201；名称1–80字 |
| GET/PATCH/DELETE /projects/:id | Project / `{name?,description?}` / 204 | DELETE软删除，有非终态运行409 |
| GET/POST /projects/:id/workflows | 列表 / `{name,description}` → Workflow | 创建默认Start/End草稿、空规格，同事务 |
| GET/PATCH/DELETE /workflows/:id | Workflow摘要/元信息/204 | 删除规则同项目 |
| GET/PUT /workflows/:id/draft | `{revision,definition,layout}` | PUT带If-Match；完整替换图/layout，规格任务不隐式删除 |
| GET/PUT /workflows/:id/specification | 规格与draftRevision | PUT带If-Match，七类规格字段，更新统一draft revision |
| GET/PUT /workflows/:id/tasks | `{items,draftRevision}` | PUT替换任务集合；关联节点仍引用已删任务时422 |
| POST /workflows/:id/validate | `{expectedRevision}` → `{valid,validatedRevision,diagnostics}` | 校验不通过仍200；版本冲突409 |
| GET/POST /workflows/:id/versions | 列表 / `{expectedRevision,label}` → version | 发布201；不通过422；相同revision返回既有版本200 |
| GET /versions/:id | 完整不可变snapshot与hash | 无修改接口 |
| GET /node-types | descriptor列表 | 不发送executor函数 |
| POST /workflows/:id/runs | 见下例 → `{runId,status,snapshotHash}` | 202，强制Idempotency-Key |
| POST /workflows/:id/node-tests | `{expectedRevision,nodeId,input}` | 202，纯计算节点限定；同样创建真实run历史 |
| GET /runs | workflowId,status,cursor → 列表 | 按createdAt降序 |
| GET /runs/:id | snapshot、node摘要、edgeStates、lastEventSeq | 一致读事务，清理后410 |
| GET /runs/:id/nodes/:nodeId | NodeRun、attempts、输入输出 | 大结果按需请求，明确1MiB限制 |
| GET /runs/:id/events | SSE after游标 | Last-Event-ID优先；协议见DATA_FLOW |
| GET /runs/:id/logs | afterSeq,limit → 事件日志页 | 不修改运行 |
| POST /runs/:id/cancel | 空对象 → 当前run状态 | 202停止中；已终态200原状态，重复取消幂等 |
| POST /runs/:id/replay | `{input?}` → 新runId | 新幂等键；固定原快照；不支持从中间节点续跑 |
| POST /approvals/:id/decision | `{expectedRevision,decision,comment,decisionRequestKey}` | APPROVE/REJECT；竞争失败409，过期409 |
| GET/POST /workflows/:id/test-cases | 列表 / `{name,input,assertions}` | 201创建，断言有稳定ID |
| PUT/DELETE /test-cases/:id | 完整用例+If-Match /204 | 独立用例revision，删除软删除 |
| GET /runs/:id/test-results | expected/actual/status/reason | NOT_EVALUATED不能当通过 |
| GET /runs/:id/export | 下载JSON快照、输出与日志 | 脱敏，原始秘密不导出 |
| GET/PATCH /settings | 注册键值 | 并发值受基线上限约束 |
| POST /maintenance/backup | `{}` → `{backupId,createdAt}` | 备份固定本机目录，不接受任意文件路径 |
| POST /maintenance/cleanup-preview | 保留策略 → `{token,count}` | 只选终态；token短期有效 |
| POST /maintenance/cleanup | `{token}` → `{deletedCount}` | 重新核验记录，不能清理活动运行 |

## 请求和响应示例

保存图（definition/layout完整结构见MODULE_DESIGN；示例仅展示外层）：

```http
PUT /api/v1/workflows/wf-uuid/draft
If-Match: "12"
Content-Type: application/json
X-CSRF-Token: local-session-token

{"definition":{"schemaVersion":1,"inputSchema":{"type":"object"},"outputSchema":{"type":"object"},"nodes":[],"edges":[]},"layout":{"schemaVersion":1,"nodes":{},"groups":[],"viewport":{"x":0,"y":0,"zoom":1}}}
```

空图允许保存为无效草稿，发布会返回缺少Start/End诊断。成功返回200及 `ETag: "13"`，body `{revision:13,savedAt:1794614400000,contentHash:"sha256:…"}`。If-Match缺失428，过期409；API不自动合并。

```json
{
  "source": {"kind":"PUBLISHED","versionId":"version-uuid"},
  "input": {"amount":50},
  "testCaseId": null
}
```

上述提交至runs端点，头 `Idempotency-Key: request-uuid`。草稿测试source改为`{kind:"DRAFT_TEST",expectedRevision:13}`，可指定testCaseId；testCase存在时使用其冻结输入，禁止再传冲突input。响应202：`{"runId":"run-uuid","status":"QUEUED","snapshotHash":"sha256:…"}`。重复键且同请求hash返回同run；不同请求409 IDEMPOTENCY_CONFLICT。键作用域为全局运行创建，保留期至少等于对应运行保留期；历史清理后不承诺旧键防重放。

```json
{
  "error": {
    "code":"WORKFLOW_INVALID",
    "message":"工作流不能发布",
    "requestId":"request-uuid",
    "details":[{
      "code":"REF_NOT_DOMINATING","severity":"error",
      "nodeId":"end","fieldPath":"bindings.result",
      "message":"引用来源可能被条件跳过",
      "suggestion":"使用Join的activeInputs，或为可选引用设置fallback"
    }]
  }
}
```

## 错误与竞争处理

| HTTP | code示例 | 客户端处理 |
|---|---|---|
| 400 | MALFORMED_JSON/INVALID_CURSOR | 定位请求参数，不自动重试 |
| 403 | ORIGIN_REJECTED/CSRF_INVALID | 重新建立本机会话，禁止跨站调用 |
| 404/410 | NOT_FOUND/HISTORY_PURGED | 区分不存在与已清理；无数据库tombstone时允许404 |
| 409 | REVISION_CONFLICT/APPROVAL_RESOLVED/ACTIVE_RUN_EXISTS | 展示当前revision/status，用户决定下一步 |
| 413 | PAYLOAD_TOO_LARGE | 提示具体字节限额 |
| 422 | WORKFLOW_INVALID/INPUT_INVALID/EXECUTOR_VERSION_MISSING | 展示节点/字段诊断 |
| 428 | PRECONDITION_REQUIRED | 补齐revision后重试 |
| 429 | RUN_QUEUE_FULL | 显示队列容量；不得换幂等键无限重试 |
| 503 | STARTING/PERSISTENCE_UNAVAILABLE | 保留本地修改和请求键，查询确认结果 |
| 500 | INTERNAL_ERROR | requestId关联本地日志，不返回堆栈或文件系统敏感路径 |

响应丢失时，创建运行重用原幂等键，取消直接重试，审批重用decisionRequestKey，保存先GET对比revision/hash，不把写请求一律自动重发。格式校验失败不创建run；运行中的节点失败通过Run状态与事件表达，不用一个已经返回202的HTTP请求追报500。

OpenAPI将从contracts schema生成；Phase1提交契约示例和错误枚举快照，前端mock与后端集成测试使用同一fixture。P0无远程认证、webhook触发、定时触发、上传脚本或执行Shell端点。
