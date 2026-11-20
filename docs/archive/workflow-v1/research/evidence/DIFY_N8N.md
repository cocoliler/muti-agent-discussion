# Dify 与 n8n 定向研究证据

研究日期：2026-11-14。本文来自实际访问的官方文档、主分支源码和测试；没有本地运行两款产品，没有进行截图像素测量。链接指向动态主分支，文档及源码可能继续变化，不将本次观察误写成固定发行版本保证。

## Dify

| 研究项 | 已确认事实 | 对 FlowStudio 的建议（原创设计） |
|---|---|---|
| 定位与核心能力 | Workflow/Chatflow 共用可视化节点系统，前者处理一次任务，后者带会话层。[D1] | 采用单次任务运行模型，不将聊天、模型调用、知识库列为基础依赖。 |
| 编辑器组织 | `workflow/index.tsx` 组合 ReactFlowProvider、自定义节点/边、节点交互 hooks、草稿同步、控制工具、选择交互和底部面板高度。[D2] | 将画布、属性、执行详情、同步逻辑拆为独立职责；不复制其多人协作状态体系。 |
| 节点视觉结构 | `nodes/_base/node.tsx` 组合图标、节点头部/正文、输入输出 handle、重试/错误组件，区分运行、成功、失败与选中边框。[D3] | 统一节点壳，标题/类型/状态分层，状态加图标和文字；尺寸由本项目制定。 |
| 条件和变量 | If/Else 有按顺序判断的 IF/ELIF 和 ELSE 兜底，可引用前序节点变量。[D4] | 采用显式引用选择器和有限表达式 AST；不执行任意 JavaScript。 |
| 合流语义 | Variable Aggregator 用于同类型的互斥分支值选择；官方明确它不合并并行分支输出。[D5] | 不能用一种含糊的“合并”同时表示值选择与并行等待；必须定义边激活状态和汇聚契约。 |
| 测试与反馈 | 单节点测试由配置面板提供输入，结果包含输入、输出、耗时和错误；Variable Inspector 可更改缓存值。[D6] | P0 提供显式 fixture 的单节点测试和结果比较；后续再做上游缓存调试，防止测试数据混入正式运行。 |
| 定义/版本/执行 | 官方区分 Current Draft、Latest Version、Previous Versions；源码分别定义 Workflow、WorkflowRun、WorkflowNodeExecutionModel，运行保存 graph、inputs、outputs 等字段。[D7][D8] | 草稿、不可变发布版本与独立运行实例分离，历史图从运行快照读取。 |
| 测试组织 | 实际读取测试目录，包含 graph_engine、nodes、变量池、人工输入、图拓扑等测试入口；单个 topology 测试正文因缓存问题未取到。[D9] | 图拓扑、人工决策、变量解析应独立测试；不声称已验证 Dify 的全部测试。 |

**不采用的复杂度。** 当前 Compose 源码包含 API/Worker、数据库、Redis、向量存储和其他基础服务配置；这证明其部署模板覆盖的范围远超本地 DAG 工具，但不表示列出的全部可选向量库必须同时安装。[D10] FlowStudio 只借鉴模块与交互思想。

**许可证事实。** LICENSE 自述为 Apache 2.0 修改版，附多租户、前端标识等条件，并有外观相关声明；不能按纯 Apache 2.0 处理。[D11] 本项目建议独立实现交互，不复制 Dify 前端代码、标识或视觉素材。

**推断边界。** 组件组合支持“画布为主、配置与运行信息辅助”的产品理解，但未运行产品、未量测其布局，所以本文不声称其固定面板宽度、可用性评分或性能实测结果。

### Dify 来源

- D1：[Workflow & Chatflow](https://docs.dify.ai/en/cloud/use-dify/build/workflow-chatflow.md)
- D2：[编辑器入口源码](https://github.com/langgenius/dify/blob/main/web/app/components/workflow/index.tsx)
- D3：[BaseNode 源码](https://github.com/langgenius/dify/blob/main/web/app/components/workflow/nodes/_base/node.tsx)
- D4：[If/Else](https://docs.dify.ai/en/cloud/use-dify/nodes/ifelse.md)
- D5：[Variable Aggregator](https://docs.dify.ai/en/cloud/use-dify/nodes/variable-aggregator.md)
- D6：[单节点测试](https://docs.dify.ai/en/cloud/use-dify/debug/step-run.md)
- D7：[版本控制](https://docs.dify.ai/en/cloud/use-dify/build/version-control.md)
- D8：[Workflow 数据模型](https://github.com/langgenius/dify/blob/main/api/models/workflow.py)
- D9：[工作流单元测试目录](https://github.com/langgenius/dify/tree/main/api/tests/unit_tests/core/workflow)
- D10：[现有部署模板，仅作研究](https://github.com/langgenius/dify/blob/main/docker/docker-compose.yaml)
- D11：[LICENSE](https://github.com/langgenius/dify/blob/main/LICENSE)

## n8n

| 研究项 | 已确认事实 | 对 FlowStudio 的建议（原创设计） |
|---|---|---|
| 定位与核心能力 | 仓库将其定位为自动化平台；工作流通过连接节点自动处理业务，执行实例另有列表与调试入口。[N1][N2] | 产品验收必须检查真实数据输出、可追踪历史和失败原因，不能只检查图可编辑。 |
| 创建与版本 | 官方当前文档说明编辑自动保存为草稿，发布锁定具体版本；生产触发使用发布版本。发布还可能涉及触发器注册及局部失败。[N3] | 保留草稿/发布版本分离；本地手动运行无需照搬异步注册外部触发器的复杂发布状态。 |
| 数据传递 | 数据以对象数组传递，常见结构含 `json`，可含 `binary`；字段映射可以创建路径表达式，节点通常逐 item 处理。[N4] | 第一版单一 JSON 值/命名端口，数组作为值处理；不默认隐式逐项执行，减少副作用次数歧义。 |
| 节点扩展契约 | `INodeType` 提供 description、execute 等能力；配置描述与执行函数明确区分，工作流接口含 nodes、connections、versionId、activeVersionId。[N5] | 共享序列化节点描述，后端维护执行器注册；浏览器不加载执行器。 |
| 执行引擎 | `WorkflowExecute` 存在执行栈、运行数据、取消信号、部分执行等机制；公开接口通过运行上下文调用节点。[N6] | 引擎独立于画布，P0 完整 DAG 执行，部分执行留后续；不复制其循环、AI 子节点和多种执行顺序兼容层。 |
| 手动测试与历史 | 文档区分 manual/production；可查看全局与单工作流历史，并把过去执行的数据带回当前草稿调试。[N2] | 显示运行来源、版本和输入；重跑创建新实例，历史保持不可改。 |
| 测试证据 | `workflow-execute.test.ts` 实际覆盖取消后不继续重试、等待重试期间取消、节点输出与执行顺序等，使用图形注释描述拓扑。[N7] | 按图拓扑建立可读用例，同时验证输出、顺序约束、取消/重试行为。 |
| 运行架构边界 | 官方 queue mode 使用主实例、Redis、多个 Worker；SQLite 的分布式 queue 组合不支持/不推荐。该事实仅限 queue mode，不意味着 n8n 所有本地模式都需要 Redis。[N8] | 不采用 queue mode；使用单 Node.js 服务内有限调度与 SQLite 持久化。 |

**许可证事实。** 主 LICENSE 为 Sustainable Use License；`.ee.` 文件另受企业许可约束，并非所有代码都是宽松开源。[N9] 本研究借鉴行为和契约思想，不直接复制实现。

**为何不是流程图工具（研究归纳）。** n8n 为图提供节点配置与数据结构契约、运行上下文、真实执行、错误/取消处理、版本隔离及可追溯记录；画布只是定义和调试这些行为的界面。FlowStudio 应具备同类闭环，功能规模可以小得多。

### n8n 来源

- N1：[GitHub 仓库](https://github.com/n8n-io/n8n)
- N2：[执行概念与历史](https://docs.n8n.io/build/understand-workflows/understand-executions.md)
- N3：[保存与发布](https://docs.n8n.io/build/understand-workflows/save-and-publish-workflows.md)
- N4：[数据结构](https://docs.n8n.io/build/work-with-data/understand-n8ns-data-structure.md)
- N5：[工作流及节点接口](https://github.com/n8n-io/n8n/blob/master/packages/workflow/src/interfaces.ts)
- N6：[WorkflowExecute 源码](https://github.com/n8n-io/n8n/blob/master/packages/core/src/execution-engine/workflow-execute.ts)
- N7：[执行器测试源码](https://github.com/n8n-io/n8n/blob/master/packages/core/src/execution-engine/__tests__/workflow-execute.test.ts)
- N8：[Queue mode](https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode.md)
- N9：[LICENSE](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)

## 访问限制与未验证事项

1. 未克隆完整仓库、未执行测试、未用浏览器实际操作产品；代码阅读不等于运行验证。
2. 主分支与部分 GitHub 目录的索引抓取时间不同；不保证全部引用属于同一个提交。
3. 初始猜测的 Dify `node-card.tsx`、n8n `packages/core/test/workflow-execute.test.ts` 路径不可访问，已改用上述成功读取的实际源码路径；Dify `test_graph_topology.py` 正文未成功读取，只确认目录存在。
4. 官方文档有旧 URL 重定向，来源清单使用实际到达的新 URL。没有把不可访问页面当证据。
