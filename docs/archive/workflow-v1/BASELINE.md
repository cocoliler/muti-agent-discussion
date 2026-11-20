# FlowStudio 设计基线

日期：2026-11-14。状态：研究后的设计建议，尚未实现；任何性能指标都是验收目标，不是实测结果。本轮不创建应用脚手架、不安装业务依赖、不初始化 Git、不部署。

## 一、统一决策

- 本地单用户、Windows 10/11 x64、浏览器 localhost；基础功能离线，首次获取 Node/npm 依赖及测试浏览器需要网络或预先准备缓存。
- npm workspaces；Node.js 24 LTS；React + TypeScript + Vite + @xyflow/react；Tailwind CSS + shadcn/ui；Zustand 编辑状态，TanStack Query 服务端状态。
- Fastify 模块化单体，一个 Node 后端进程同时提供静态资源、API、调度与执行；开发期另有 Vite 进程。SQLite + Drizzle + better-sqlite3，短同步事务，无事务内 await。驱动的 Windows 预编译包与 SQLite 修复版本是 Phase 1 必验门槛。
- 不需要 Docker、Redis、消息队列、云服务、Nginx。CPU Worker Threads/本地独立 Worker 为后续按测量扩展，均非首版必要进程；不执行任意用户 JS/Shell，不把线程当安全沙箱。
- HTTP 命令 + SSE 状态。状态及事件同事务提交，事件以 runId+seq 去重补发；中断连接不取消运行。
- 草稿可修改，发布版本不可变，所有运行有不可变快照。草稿测试也先保存并冻结快照；历史画布只读。编辑坐标/分组属于 layout，执行定义不依赖 React Flow 对象。
- 同一工作流一个规格文档和任务列表；规格含目标/描述/输入/输出/规则/验收/异常处理。TaskItem 人工拆解，可映射多个节点；一个节点至多关联一个任务；任务依赖须能由图路径解释。不自动用 LLM 分解。
- P0：项目/工作流 CRUD 与搜索、规格与手动拆解、完整基础画布（分组、复制、多选、撤销/重做、自动布局）、校验/发布、真实执行/审批/取消/重试/超时、SSE/历史/断线补发、断言与测试、备份与本地检查。
- P0 节点：Start、Transform（白名单 JSON 字段映射/有限运算）、Template、Condition（true/false）、Parallel、Join、Approval、Assert、End；HTTP GET 为 P1，可用离线 JSON 数据完成整个 P0。任意脚本执行、LLM、循环、子工作流、凭据库为 P2 或另行评估；云部署、多租户、多人实时编辑、分布式执行为 Out of Scope。
- 分组仅视觉，不是子流程；P0 无嵌套分组。自动布局采用分组折叠为超节点后两级 Dagre 布局，跨组边仍需验证，不能宣称库自动解决。

## 二、执行语义权威约定

- DAG 单 Start、单 End。普通节点单控制入边；多入边必须显式 Join，条件节点恰有 true/false 两类出口，Parallel 激活全部出口。
- 每条控制边在一次运行中为 UNRESOLVED、ACTIVE、INACTIVE；ACTIVE 仅表示源节点成功且选中了此出口。Condition 未选边 INACTIVE；SKIPPED 节点所有出边 INACTIVE。
- Join 等所有入边 resolved；至少一个 ACTIVE 才执行，零 ACTIVE 则 SKIPPED。并行的全部激活支路因此会被等待，互斥分支的跳过信号不会形成死锁。失败不等于 INACTIVE；首版最终失败走全局 fail-fast。
- 输入引用是结构化 nodeId/port/JSON Pointer，需从控制上游读取；可能跳过的源必须显式可选并提供 fallback，或经过 Join 的 activeInputs 汇总。必需引用要求源支配目标；不隐式增加控制边。
- Run：QUEUED、RUNNING、WAITING_APPROVAL、CANCELLING、SUCCEEDED、FAILED、CANCELLED、INTERRUPTED。
- NodeRun：PENDING、READY、RUNNING、RETRY_WAIT、WAITING_APPROVAL、SUCCEEDED、FAILED、SKIPPED、CANCELLED、INTERRUPTED；重试的尝试记录单独存 NodeAttempt。
- 每次 NodeRun 只有一个当前 attempt/token；条件更新抢占，唯一约束防重复；只承诺本进程中防重复调度，不承诺外部副作用 exactly-once。
- 默认全局最多 4 个执行节点，每 run 最多 2；最多接纳 2 个非终态运行（审批等待也占用已接纳运行名额），队列最多 20。审批与重试等待不占节点槽。
- 自动重试仅纯计算/显式幂等执行器且错误可重试，最多 3 次总尝试（默认 1）；退避 1s、2s，上限 30s。默认节点超时 30s，审批截止 24h；不提供任意时刻暂停。
- 审批属于持久等待点；拒绝令节点 FAILED(APPROVAL_REJECTED)，运行 fail-fast。只在所有剩余活动均为审批等待、无执行/可运行/重试计时任务时 Run 才 WAITING_APPROVAL。
- 重启时全部未终态运行（含 QUEUED 与审批等待）标记 INTERRUPTED，节点未终态同理，撤销未决审批；保留已完成输出，不自动续跑。用户可基于原快照创建新运行，明确可能重复外部效果。
- 用户取消先提交 CANCELLING、递增 fencing generation，禁止新调度、发 AbortSignal；最多 5s 收敛后 CANCELLED；晚到结果不覆写终态。无法回滚已经发生的外部效果。失败先关闭调度并取消其他节点，5s 内收敛 FAILED。
- 全部节点终态、End 成功、断言通过才能 SUCCEEDED；全部未激活路径允许 SKIPPED。无可运行/无等待/无计时器却剩 unresolved 属 ENGINE_DEADLOCK，不能无限挂起。

## 三、数据与范围约定

- UUID 字符串 ID，时间 UTC 毫秒整数，UI 转本地时区；状态枚举用 TEXT CHECK；JSON 使用 TEXT+json_valid 与服务层 schema 双校验。
- Project、Workflow、WorkflowDraft、TaskSpecification、TaskItem、WorkflowVersion、WorkflowRun、NodeRun、NodeAttempt、ExecutionEvent、ApprovalRequest、TestCase、TestResult、AppSetting；WorkflowNode/WorkflowEdge 嵌入定义 JSON，不另建双份节点表；边运行状态保存在 run 的 edgeStates JSON。
- Draft revision 是规格/任务/定义/layout 统一乐观并发令牌，任何修改都递增；发布与运行取一致快照。published version 的 definition/layout/spec/tasks 全部冻结；测试用例本次断言也冻结到运行。
- 数据保存在仓库 .flowstudio/（忽略 Git，可通过 FLOWSTUDIO_DATA_DIR 改为本机绝对路径）。数据库不可放网络共享或同步盘。删除项目/工作流使用软删除；历史按明确清理策略物理删除。
- P0 上限：200 执行节点、400 边、20 视觉组；单节点 JSON 输入/输出各 1 MiB；定义请求 2 MiB；每运行结构化事件至多 10,000 条。超过限额有明确错误。
- 性能验收基线：Windows x64、4 核、16GB RAM、SSD、当前稳定 Chromium；200 节点拖动目标 >=30fps，保存 API p95 <500ms，提交到画布状态 p95 <500ms；需实施阶段测量。

详设若需变更本基线，必须同时更新关联产品、API、状态机、测试和任务；不能在单个文档另起术语。
