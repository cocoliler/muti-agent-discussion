# Flowise 与 Node-RED 定向研究证据

访问日期：2026-11-14。此记录为设计前研究，不代表已安装、运行或对竞品进行完整测试。来源使用可访问的官方文档、GitHub 与 raw 源码；分支 URL 会变化，未取得可信 commit SHA，不能视为不可变快照。事实、推断和建议分别标记。

## Flowise

### 1. 定位与功能（事实）

官方定位是可视化 AI Agent / LLM 工作流开发平台，区分 Assistant、Chatflow、Agentflow 三类构建方式，并列出追踪、评估和人工介入能力。它的目标明显比通用有限 DAG 执行更偏 AI 编排。[官方介绍](https://docs.flowiseai.com/)

本次访问 GitHub 首页显示仓库为 Public archive，并显示归档日期 2026-08-13；只记录页面观察，不推断归档原因、产品生命周期或社区未来。[仓库](https://github.com/FlowiseAI/Flowise)

### 2. 产品交互特点（源码事实）

`CanvasNode.jsx` 将标题、图标、输入/输出、复制、删除、信息和升级警告组织在自定义卡片中；高级参数进入额外对话框。节点选择改变边框。这里研究的是可访问的 Chatflow canvas 模块，不能把其所有细节直接等同于 Agentflow V2。[CanvasNode](https://raw.githubusercontent.com/FlowiseAI/Flowise/main/packages/ui/src/views/canvas/CanvasNode.jsx)

`NodeInputHandler.jsx` 根据配置类型引用下拉、多选、异步选择、文件、JSON、代码等输入控件；配置可展开到独立对话框。这说明参数元数据与表单控件之间存在映射，不能据此声称已经有标准 JSON Schema 通用渲染器。[NodeInputHandler](https://raw.githubusercontent.com/FlowiseAI/Flowise/main/packages/ui/src/views/canvas/NodeInputHandler.jsx)

### 3. 关键架构（源码事实）

`views/canvas/index.jsx` 引用 `reactflow` 的 `useNodesState`、`useEdgesState`、Controls、Background，注册 CanvasNode 与 ButtonEdge。保存路径调用 `reactFlowInstance.toObject()`，将对象 JSON 序列化为 `flowData`；连线同步更新节点输入引用；新增节点经 `initNode` 初始化。[Canvas](https://raw.githubusercontent.com/FlowiseAI/Flowise/main/packages/ui/src/views/canvas/index.jsx)

`Interface.ts` 定义 INodeProperties（name、label、version、icon、category 等）、INodeParams（type、optional、default、options、show/hide 等），INode 提供 inputs、output、loadMethods、init/run。该文件同时包含大量 AI 相关契约；并非适合原样照搬的最小领域接口。[节点接口](https://raw.githubusercontent.com/FlowiseAI/Flowise/main/packages/components/src/Interface.ts)

`NodesPool.ts` 扫描组件包 `dist/nodes`，加载含 nodeClass 的 JS 模块，构造实例并建立按名称索引的字典，同时处理禁用节点和类别过滤。此模式可解释节点目录与运行时注册之间的关系。[NodesPool](https://raw.githubusercontent.com/FlowiseAI/Flowise/main/packages/server/src/NodesPool.ts)

### 4. 校验与扩展（源码事实）

`genericHelper.js` 的 `initNode` 将参数区分为 inputParams 和 inputAnchors；`isValidConnection` 比较端口类型并处理单输入/列表输入；`isValidConnectionAgentflowV2` 排除自连接及新连接形成的循环。这里看到的是局部编辑器校验，不能据此推断完整服务端静态验证已覆盖不可达节点、引用必达性等问题。[通用辅助函数](https://raw.githubusercontent.com/FlowiseAI/Flowise/main/packages/ui/src/utils/genericHelper.js)

### 5. 值得借鉴（推断）

从上述组件与元数据分工可推断：统一的节点描述能减少每新增节点时修改画布核心代码的次数；将节点版本放入定义有利于发现旧流程配置迁移需求。此收益是设计分析，未通过定量维护成本实验验证。

### 6. 不适合直接引入的部分（建议）

FlowStudio 不应复制 AI 供应商、向量存储、聊天会话和企业工作区依赖。其服务端 package.json 含多类数据库、Redis/BullMQ 与云 SDK 依赖，但“存在依赖”不等于“所有启动模式都强制需要这些服务”。FlowStudio 应只提取注册机制，不继承整套依赖。[服务端依赖及脚本](https://raw.githubusercontent.com/FlowiseAI/Flowise/main/packages/server/package.json)

### 7. 许可证与测试证据边界（事实）

许可证将 enterprise 目录与显式版权标注文件（如 IdentityManager.ts）列为商业许可，其余受列明条件约束的部分为 Apache-2.0；不能称全仓库都是 Apache-2.0。本轮仅借鉴机制，不复制源码。[LICENSE.md](https://raw.githubusercontent.com/FlowiseAI/Flowise/main/LICENSE.md)

服务端 package.json 提供 Jest 与 Cypress 脚本，确认测试入口存在；尝试的 `packages/server/test/NodesPool.test.ts`、`__tests__` 与 Jest 配置路径未返回有效内容，**未阅读到对应测试断言，未运行测试，不评价覆盖率**。不把失败路径列为已确认模块。

### 8. 落实到 FlowStudio（原创建议）

- 建立显式、编译期的节点注册表，P0 不从用户目录动态执行第三方 JS。
- 节点描述声明配置 Schema、端口、图标与 executorVersion；前端仅接收可序列化描述。
- 画布节点展示摘要，复杂表单放右侧属性面板，避免节点随参数数量无限增长。
- 服务端使用同一契约执行全量校验；前端连线校验只负责即时反馈。
- 编辑布局与执行定义分字段保存；不将 React Flow 对象当成引擎领域模型。

## Node-RED

### 1. 定位与功能（事实）

Node-RED 是事件驱动应用的低代码工具。官方 Windows 文档支持安装 Node.js、安装包、运行命令的本地使用方式；其文档包含旧版本示例和过时的构建工具段落，所以本项目仅采纳“本地 Node.js 启动”模式，不复制文档中的旧 Node 版本或旧 Windows Build Tools 命令。[仓库](https://github.com/node-red/node-red)；[Windows 指南](https://nodered.org/docs/getting-started/windows)

### 2. 产品交互（文档事实）

节点编辑定义包括 palette 分类、可编辑属性、图标、输入输出数以及编辑模板和帮助文字；编辑器和运行时通过相同 node type 对应。[HTML 节点定义](https://nodered.org/docs/creating-nodes/node-html)

Debug 侧栏可按全部节点、选中节点和当前流程过滤，最多显示最近 100 条消息；暂停侧栏会丢弃新消息，并报告丢弃数量。这是实时观察工具，不等同于持久化执行审计记录。[Debug 侧栏](https://nodered.org/docs/user-guide/editor/sidebar/debug)

### 3. 关键架构（文档与源码事实）

节点 `.js` 文件定义运行时行为，通过 `registerType` 注册；节点接收 input 事件，使用 send 发送消息，使用 done 表达处理完成或错误，并在 close 时清理资源。多输出可分别发送消息。[运行时节点](https://nodered.org/docs/creating-nodes/node-js)

运行时 `Flow.js` 组织 flow 内节点的创建、启动、停止，并维护 activeNodes、subflowInstanceNodes、catchNodes、statusNodes 等集合。这确认轻量部署下仍可有清晰的运行时模块边界；不代表该类本身具备数据库持久 DAG 调度语义。[Flow.js](https://raw.githubusercontent.com/node-red/node-red/master/packages/node_modules/@node-red/runtime/lib/flows/Flow.js)

消息是可带属性的 JavaScript 对象，通常使用 payload；官方强调不要删除与当前节点职责无关的属性。该模式适用于持续事件流，但 FlowStudio 的每次有限运行、不可变节点输出和版本快照需要另行定义。[消息设计](https://nodered.org/docs/developing-flows/message-design)

### 4. 流程组织与复用（文档事实）

Projects 将 flow 文件、依赖和 Git 组织为可分发应用；文档说明该能力需启用并依赖 Git/ssh-keygen。可安装节点由 Palette Manager 或 npm 管理，依赖写入 package.json。本项目无需将“运行时项目管理”做成 Git GUI。[Projects](https://nodered.org/docs/user-guide/projects/)；[添加节点](https://nodered.org/docs/user-guide/runtime/adding-nodes)

### 5. 借鉴机制（推断）

本地 Node.js、模块化编辑器/运行时和节点消息契约可以同时存在，说明“专业”不必依赖分布式基础设施。独立 debug 面板与节点状态能降低定位问题的操作成本；但需补充运行 ID、尝试号与持久化事件后才能满足本项目追溯要求。

### 6. 不适用点（原创判断）

不能把事件消息的多次触发、部署时替换节点生命周期直接转换成单次 DAG 的节点状态机；P0 不引入 MQTT 常驻监听、插件市场、任意 Function 代码、复杂上下文存储插件和内置 Git 管理器。

### 7. 许可证、测试与限制（事实）

Node-RED 的根 LICENSE 为 Apache-2.0。[许可证](https://raw.githubusercontent.com/node-red/node-red/master/LICENSE)

根 package.json 声明 Mocha、nyc、测试构建脚本，test/unit 目录可访问并显示 @node-red 子目录；多次尝试具体 Flow 测试文件 raw 地址未取得有效内容，**仅确认测试组织和入口，没有阅读到这些测试断言或执行测试**。[package.json](https://raw.githubusercontent.com/node-red/node-red/master/package.json)；[test/unit](https://github.com/node-red/node-red/tree/main/test/unit)

### 8. 落实到 FlowStudio（原创建议）

- 保持单个 Node.js 应用即可启动的体验，把 API、引擎、调度器做成逻辑模块而非独立服务。
- 编辑器类型与后端执行器使用相同 type/version 键；运行行为不放在 React 组件内。
- 调试 UI 借鉴过滤与节点定位；历史由 SQLite 持久化事件和节点尝试记录提供。
- 明确 finite DAG 语义，分支未激活时传播跳过，不能等候不存在的消息。
- Git 只用于团队开发源码；用户的本地工作空间仍由应用数据库管理。

## 研究限制

没有克隆整个仓库，没有运行竞品、浏览器交互测试或截图评估。对 UI 的结论来自可访问源码及文档描述，不能声称做过视觉像素对比。网络工具部分目录与测试文件返回 Cache miss/404；PowerShell 直连 GitHub API 被环境网络限制，未取得 commit SHA。正式引入依赖或复用源码前应重新锁定版本并复查对应许可证。
