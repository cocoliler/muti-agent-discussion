# 模块与扩展契约

## 职责和边界

| 模块 | 对外接口（概念签名） | 依赖/数据 | 错误边界 |
|---|---|---|---|
| Project Management | create/list/update/archiveProject | ProjectRepo | 名称/项目不存在/活动运行冲突 |
| Specification Management | saveSpec/saveTasks(expectedRevision) | DraftRepo、TaskSpec/TaskItem | 原子 revision 冲突、任务依赖环 |
| Workflow Designer | dispatch(EditCommand)、serializeDraft | contracts、node-catalog 描述、React Flow adapter | 本地表单未提交/图非法，可保存但禁发布 |
| Workflow Definition | decode/migrate/normalize/hash | 纯 JSON 契约，无 React/DB | 未知 schemaVersion、无效引用 |
| Node Registry | getDescriptor/getExecutor(type,version) | 内置描述和执行器映射 | 未知类型/版本拒绝，不运行任意路径 |
| Workflow Validator | validate(snapshot): Diagnostic[] | Definition、Registry 描述 | 返回结构化诊断，不抛用户配置异常 |
| Execution Engine | createRun/advance/cancel/decideApproval | repo port、Scheduler、Registry executor | 统一 ExecutionError，终态不可逆 |
| Task Scheduler | wake/tick/claim/release | Engine 的持久状态接口 | 并发上限、定时器过期、拒绝已停运行 |
| Execution History | listRuns/getRun/getNodeAttempts | 只读 repository | 删除/保留窗口/分页 |
| Runtime Events | appendInTx/streamAfter | ExecutionEvent 表 | 断连不影响任务，慢客户端断流补发 |
| Local Persistence | transaction、repositories、backup | Drizzle/SQLite/本机目录 | BUSY/FULL/IOERR 转持久层错误，禁止假成功 |

依赖方向：`web → contracts + workflow-core(纯校验) + node-catalog(descriptors)`；`server routes → application services → engine/core + repository ports`；`SQLite adapter → ports`。核心引擎不导入 Fastify、React、Drizzle；组合根注入时钟、ID、日志、repository、executorRegistry，方便用虚拟时钟验证调度。避免把所有模块做成互相注入的服务图。

## 统一数据契约（设计示意，非实现文件）

```typescript
type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type ValueBinding =
  | { kind: 'literal'; value: Json }
  | { kind: 'reference'; nodeId: string; port: string; pointer: string;
      optional: false }
  | { kind: 'reference'; nodeId: string; port: string; pointer: string;
      optional: true; fallback: Json };
interface WorkflowDefinition {
  schemaVersion: 1;
  inputSchema: JsonSchemaSubset;
  outputSchema: JsonSchemaSubset;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[]; // 全部为控制边；数据引用放在 bindings
}
interface WorkflowNode {
  id: string; type: string; typeVersion: number; label: string;
  taskId?: string; config: Json; bindings: Record<string, ValueBinding>;
  retry: { maxAttempts: 1 | 2 | 3 }; timeoutMs: number;
}
interface WorkflowEdge {
  id: string; source: string; sourcePort: string;
  target: string; targetPort: string;
}
interface WorkflowLayout {
  schemaVersion: 1;
  nodes: Record<string, { x: number; y: number; groupId?: string }>;
  groups: { id: string; title: string; x: number; y: number;
    width: number; height: number }[];
  viewport: { x: number; y: number; zoom: number };
}
```

JSON Schema 子集支持 type、properties、required、items、enum、additionalProperties、长度/数值上下界；不支持远程 `$ref`、任意组合递归或自定义执行函数。schema 对齐由 contracts 中唯一规范生成 TS 类型和 API 校验，禁止手写三份 DTO。

## 节点定义与执行器

```typescript
interface NodeDefinition {
  type: string; version: number; displayName: string;
  icon: string; category: 'input'|'task'|'control'|'validation';
  configurationSchema: JsonSchemaSubset;
  uiSchema: { fields: FieldDescriptor[] };
  inputPorts: PortDescriptor[]; outputPorts: PortDescriptor[];
  inputSchema: JsonSchemaSubset; outputSchema: JsonSchemaSubset;
  effect: 'pure' | 'external';
  supportsRetry: boolean;
  validate(node: WorkflowNode, graph: ReadonlyGraph): Diagnostic[];
}
interface NodeExecutor {
  type: string; version: number;
  execute(ctx: {
    runId: string; nodeRunId: string; attempt: number; attemptToken: string;
    input: Json; config: Json; signal: AbortSignal;
    deadlineAt: number; log: (entry: SafeLogEntry) => void;
  }): Promise<
    | { kind: 'completed'; output: Json; activePorts: string[] }
    | { kind: 'approval'; prompt: string; deadlineAt: number }
  >;
}
```

`validate` 为纯函数、前后端共用；`execute` 仅服务端导入。前端节点 renderer 通过 `type@version → descriptor + renderer` 映射，后端通过相同键找到 executor。UI schema 是受控字段类型（文本、数值、枚举、变量选择、映射表），不能嵌入脚本。共享包使用显式 exports，浏览器打包不导入服务端实现。节点新版本不自动修改历史定义；迁移显式复制草稿、校验、再发布。

执行器不能自行更新运行状态、插入事件或调度下游；只能返回输出/激活出口或审批等待要求。引擎统一处理 schema、attempt token、事务和事件。已超时执行器晚返回，其 token 失效，不能更新成功状态。

## 首版节点语义

| type@1 | 输入/配置 | 输出/控制 | 失败边界 |
|---|---|---|---|
| start | run 输入 JSON + inputSchema | 原样 `data`；单 `out` | 运行前拒绝 schema 不匹配 |
| transform | 字段映射、trim/upper/lower、有限数值算术 | `data`；单 out | 类型不符/除零/大小超限；无任意表达式 |
| template | 文本及变量绑定 | `{text}`；单 out | 缺少必需值；不执行 HTML/代码 |
| condition | `left/operator/right` AST；eq/ne/gt/gte/lt/lte/exists | `{matched}`；true/false 各一边 | 类型不可比较；无 eval |
| parallel | 无配置，2–8 个 branch 端口 | 输入透传；所有端口 ACTIVE | 出口不足或分支不能到 End |
| join | 2–8 入边；每输入源命名 | `{activeInputs:[{edgeId,nodeId,value}]}`；单 out | 按边 ID 排序，不使用完成先后顺序；条件可跳过输入 |
| approval | 提示、24h 默认截止 | 同意后 `{approved:true,comment}`；单 out | 拒绝/过期终止；不自动重试 |
| assert | pointer、operator、expected | `{passed:true,actual}`；单 out | 不符合即 ASSERTION_FAILED，保留预期/实际 |
| end | output 映射及 outputSchema | 最终输出；无出口 | 输出 schema 不匹配 |

Start/End 唯一；普通节点单控制输入，Parallel/Condition 以外节点单输出。视觉 Group 不出现在执行节点注册表。单节点测试仅支持 Transform/Template/Condition/Assert，用户提供输入样本，不偷偷执行上游；Approval 和控制结构通过工作流测试覆盖。

## 规格与任务映射

每个工作流有一份可编辑规格，任务记录 `id/title/responsibility/inputSchema/outputSchema/completionCriteria/dependencyTaskIds/order`。节点 `taskId` 可空；一个任务可以关联多个节点。将任务生成节点只是显式编辑命令，可撤销；更改任务依赖不会静默改图。

发布校验：需要执行的任务至少一个关联节点，任务依赖必须在节点 DAG 中存在相应路径（上游任务所有出口到下游入口的规则由显式任务映射校验确定：每个下游关联入口须能从至少一个上游关联出口到达）；若任务声明“必须完成”，还需支配关系，不能仅有条件路径。任务未映射标 `TASK_UNMAPPED`。验收标准可关联 Assert 节点或 TestCase 的断言 ID；纯文本标准保留人工验收，不伪装自动判断。
