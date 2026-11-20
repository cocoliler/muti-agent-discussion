# Git 与工程协作

本轮未初始化 Git、未建立应用脚手架。本文定义后续实现规范。团队协作指共同开发源码，运行中的 FlowStudio 仍是本地单用户应用，不建设多人协同编辑服务。

## 仓库结构与所有权

```text
apps/
  web/                    # React 页面、编辑器、监控、样式
  server/                 # Fastify 路由、应用服务、进程生命周期
    src/persistence/      # Drizzle 表、迁移、repository 与事务
packages/
  contracts/              # DTO、Schema、错误码、事件、图定义
  workflow-core/          # 定义、校验、计划与调度语义；不引用 React/Fastify
  node-catalog/           # 内置节点描述与后端执行器，独立导出入口
  test-fixtures/          # 纯 JSON 工作流、输入与预期
tests/
  integration/            # 临时 SQLite 与 API
  e2e/                    # 浏览器完整流程
scripts/                  # Node 跨平台开发/维护/检查脚本
docs/                     # 设计与验收证据
references/               # 用户提供参考资料，注明来源
.flowstudio/              # 本地数据库/日志/备份，必须忽略 Git
```

packages/node-catalog 的 `descriptors` 入口只能包含可序列化 Schema/元数据；`executors` 入口仅供后端。前端不能间接打包 fs、数据库或执行器。目录所有权是首要评审责任，不是禁止其他成员修改；跨目录改动在任务卡说明并邀请相应评审。

## 3～5人分工示例

|角色|主要交付|与他人的契约|建议首批任务|
|---|---|---|---|
|A：前端/UX|八类页面、画布、属性表单、交互|消费 contracts、节点描述与 REST/SSE|T2.1–T3.4|
|B：API/持久层|项目规格、草稿版本、事务、历史与设置|提供 typed API 和 repository|T1.2、T4.1–T4.3|
|C：引擎|静态校验、计划、调度、内置节点|只依赖 contracts 与 repository 接口|T1.1、T5.1–T5.4|
|D：质量/集成（第4人）|测试夹具、故障注入、Windows/E2E验收|以公开 API 测系统，不调用页面内部状态|T1.3、T6.3、T7.2、T8.1|
|E：产品与整合（第5人）|规格任务体验、文档、可访问性、发布检查|维护需求到验收追踪|T2.2、T4.1、T7.1、T8.2|

三人团队由 A 承担 UI 验收，B 承担安装/数据验证，C 承担引擎故障测试；涉及状态机或迁移的 PR 必须由另一人复核。五人团队可并行做工作台/编辑器和 API/引擎，但不要同时更改 contracts 中同一概念的结构。

## 分支、提交和 PR

- `main` 始终可安装、构建且通过当前已有核心检查；短生命周期 `feat/T5.2-branch-join`、`fix/T6.3-sse-gap`、`docs/engine-cancel`。不设长期 develop/release 多层分支。
- 每个 PR 完成一个可观察行为或一个契约迁移；通常1–3个工作日可审。较大改动先拆纯契约、后端实现、前端接入，使用向后兼容接口，不把半成品按钮暴露给用户。
- 提交示例 `feat(engine): resolve inactive branch at join`、`fix(api): reject stale draft revision`、`docs(architecture): define restart interruption`。正文说明行为与相关任务 ID。
- PR 描述包含触发条件、最终行为、改动范围、测试命令及实际结果、关联设计、迁移/兼容性；错误修复附最小重现。未跑的检查写“未运行+原因”。
- 普通 PR 至少1人 review；引擎状态机、数据迁移、共享契约变化需领域所有者 review。不得用“UI 看起来正常”替代执行数据证据。
- 推荐 squash merge，任务号保留在提交说明；合并前更新 main 并跑受影响检查。禁止强推共享 main。

## 接口契约先行

1. 以 contracts Schema 为共同依据：枚举、字段、JSON Pointer、错误路径和 SSE seq 必须显式定义。
2. 修改契约先附成功/失败 JSON fixture；评审者验证与设计基线一致，再并行实现生产者和消费者。
3. 前端可用 fixture 临时开发，但数据源标记清楚，接口可用后删除演示结果逻辑；不得合并“后端缺失时自动假成功”。
4. 编译检查防止旧字段遗留；运行时 Schema 校验防止来自磁盘/HTTP 的未知 JSON 越界。
5. API 版本前缀是传输契约版本；definitionSchemaVersion、节点 executorVersion 与 workflowVersion 是不同版本维度，不能共用一个字段。

## Migration 协作

- `apps/server/src/persistence/migrations` 只由一个 PR 在同一合并窗口维护，其他功能 PR依赖已合并迁移。用时间戳/唯一编号排序并记录生成工具版本。
- 已进入 main 或被他人应用的迁移不可改写；新增前向修复迁移。未合并分支的迁移冲突可重新生成，但必须在临时数据库重跑全链。
- 每个迁移 PR 验证空库→当前、上一版本fixture→当前、失败回滚、foreign_key_check；会影响已有数据时写备份/恢复步骤。
- 不提交 `.db`、`-wal`、`-shm`、真实运行日志或用户输入。测试数据使用可复现 JSON 与临时 DB。
- 应用不会替用户自动降级数据库。恢复旧备份需配套旧版应用，并先在新目录验证。

## 合并冲突处理

先确认语义，再处理文本。共享枚举冲突由契约责任人确定唯一结果；迁移按上节处理；画布 fixture 冲突不得简单保留一方节点 ID。package-lock 冲突通过统一 Node/npm 环境在保留正确依赖声明后重新生成，并复查依赖树。禁止为了消除冲突删除另一功能的断言。

多人或多个 Codex 实例同时工作时，各任务使用独立分支/工作目录更易隔离。若必须共享目录，先声明拥有的文件与接口，不同时改同一文件；独立目录的结果通过正式 PR 汇合。共享工作目录内看到别人的改动，不自动恢复或格式化掉。

## 本地质量门槛

未来 `npm run check` 聚合格式、lint、typecheck、单元/集成测试；`npm run test:e2e` 与 `npm run build` 为合并核心 UI/API 改动的附加门槛。脚本均可在 PowerShell 执行，GitHub Actions 仅可选辅助。检查失败必须修复或在任务范围内清楚记录阻塞，不能修改测试使失败被隐藏。覆盖率用于发现盲区；调度不变量、事务和异常案例通过才是发布条件。

新功能完成的定义：契约/设计同步、真实行为可演示、必要测试有结果、错误状态可见、数据迁移安全、可在本地运行。新增功能不应附带与任务无关的大规模重构。
