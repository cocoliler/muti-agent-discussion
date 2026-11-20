# React Flow 定向研究证据

研究日期：2026-11-14。范围：官方能力文档、公开示例、当前主分支包元数据与两个核心组件。没有克隆全仓库、运行示例、访问 Pro 源码或执行其测试。本文中“事实”表示已读取来源；“判断/建议”不是上游承诺。动态 `main` 链接仅代表访问时状态，实施时需在锁文件固定版本。

## 项目与能力边界

**事实**：React Flow 是基于 React 的节点界面库，不提供工作流后端。[包元数据](https://raw.githubusercontent.com/xyflow/xyflow/main/packages/react/package.json) 显示包名 `@xyflow/react`、版本 `12.12.1`、React peer dependency `>=17`。核心为 [MIT 许可](https://raw.githubusercontent.com/xyflow/xyflow/main/LICENSE)，分发时保留要求的版权和许可声明。Pro 示例有独立许可，不能把“核心 MIT”理解为所有示例源码都可免费取得。

| 目标能力 | 已确认事实与来源 | FlowStudio 需要承担的工作（建议） |
|---|---|---|
| Custom Nodes | `nodeTypes` 将类型映射为 React 组件，数据经 `data` 传入；定义应保持引用稳定。[文档](https://reactflow.dev/learn/customization/custom-nodes) | 设计节点卡片、运行状态投影、配置摘要；业务数据不与 React 组件耦合 |
| Custom Edges | 组件通过 `edgeTypes` 注册，可使用 SVG 路径工具与 `BaseEdge`。[文档](https://reactflow.dev/learn/customization/custom-edges) | 条件标签、选中态、错误边、运行路径可视化 |
| Handles | 支持 source/target；同类多端口需唯一 `id`，边使用 `sourceHandle/targetHandle`；动态端口要更新节点内部测量。[文档](https://reactflow.dev/learn/customization/handles) | 端口契约、数据类型检查、禁止环和非法方向；库不会推导业务类型 |
| Node Grouping | `parentId` 表达相对位置，父节点必须先于子节点；`extent: 'parent'` 可约束移动。[文档](https://reactflow.dev/learn/layouting/sub-flows) | 分组/解组命令与坐标转换；组只影响视觉，不表示可执行子流程；高级 Selection Grouping 是 [Pro 示例](https://reactflow.dev/examples/grouping/selection-grouping) |
| Drag and Drop | 面板外拖入不内建，公开示例演示 HTML Drag/Drop、Pointer Events 等。[示例](https://reactflow.dev/examples/interaction/drag-and-drop) | 节点面板、投放位置换算、点击添加替代操作 |
| Save and Restore | `toObject` 或本地 nodes/edges 可供序列化。[示例](https://reactflow.dev/examples/interaction/save-and-restore) | 保存 API、SQLite、版本控制、冲突处理、业务 schema；不得把浏览器示例当可靠持久化 |
| Undo/Redo | 官方示例采用快照历史，并明确标记 Pro 和独立 Pro 许可。[示例](https://reactflow.dev/examples/interaction/undo-redo) | 自行实现有限历史、一次拖拽一次事务、表单合并，不能宣称核心库内建 |
| Auto Layout | 布局由外部库提供；Dagre 简单，ELK 功能多；官方指出 Dagre 在子组与组外相连时有限制。[比较](https://reactflow.dev/learn/layouting/layouting) | P0 可用平面 Dagre，按视觉组重算包围框；若要求组布局约束，单独评估 ELK，不使用 Pro 示例作必要依赖 |
| MiniMap | 核心组件通过 SVG 展示节点和视口，可配置交互与颜色。[API](https://reactflow.dev/api-reference/components/minimap) | 状态摘要、显隐偏好、颜色之外的文本替代 |
| Selection/Keyboard | 核心提供选择、多选、焦点、删除、平移/缩放配置。[API](https://reactflow.dev/api-reference/react-flow) | 与表单编辑快捷键隔离；复制/粘贴、分组、撤销均需产品实现；运行只读画布禁修改 |

## 源码阅读记录

1. [container/ReactFlow/index.tsx](https://raw.githubusercontent.com/xyflow/xyflow/main/packages/react/src/container/ReactFlow/index.tsx)：已读组件组合与参数默认值；包含 `GraphView`、`StoreUpdater`、`SelectionListener`、`A11yDescriptions`。默认删除键 Backspace、多选键按平台区分、平移激活键 Space。**判断**：FlowStudio 应显式设置并统一文档快捷键，不能依赖用户猜测库默认值。
2. [components/StoreUpdater/index.tsx](https://raw.githubusercontent.com/xyflow/xyflow/main/packages/react/src/components/StoreUpdater/index.tsx)：已读受控 props 同步至内部 store 的逻辑，nodes/edges 通过专用 setter 更新。**建议**：应用持有一份受控编辑状态；不要额外创建与之竞争的全局 nodes 副本。
3. [官方测试指南](https://reactflow.dev/learn/advanced-use/testing) 已访问；本轮未实际运行测试，也未据此声称组件已验证兼容。实施时将真实浏览器的拖拽、连接与尺寸测量列为验收重点。

首次猜测路径 `packages/react/src/components/ReactFlow/index.tsx` 返回 404；随后定位并读到实际 `container/ReactFlow/index.tsx`。这不属于“源码不可访问”。

## 定位、交互与取舍

- **定位/核心功能（事实）**：节点和连线的可交互展示、视口与选择操作；不是流程调度器、数据库或动态表单框架。
- **交互特点（事实）**：支持受控 nodes/edges、更细粒度事件、合法连接回调、键盘可聚焦元素。详见上述 API 与 [可访问性说明](https://reactflow.dev/learn/advanced-use/accessibility)。
- **架构特点（事实）**：React 外壳、图视图、状态同步、辅助无障碍和选择监听分工；依赖 `@xyflow/system` 与 Zustand。
- **值得借鉴（建议）**：编辑器 shell、画布适配器、业务命令、运行覆盖层分别组织，节点定义不直接存 React 元素。
- **不适用的扩展（判断）**：多人协作示例、任意嵌套执行子流程和昂贵 Pro 模板不是本地单用户产品的必要条件。
- **落实方案（建议）**：核心库 + 自研历史与保存协议 + 外部布局库；把核心库能力、自研工作和许可边界明确写入开发任务。

## 前端兼容性补充

- [React 官方版本页](https://react.dev/versions) 访问时显示 19.3；这只是调研快照，不证明与选定全部 UI 依赖已集成测试。
- [Vite 入门](https://vite.dev/guide/) 当前写明 Node.js 20.19+ 或 22.12+，部分模板可能更高。项目应选择仍受维护且覆盖 Windows 支持的 Node LTS，并在安装冒烟测试后固定准确版本。
- **建议**：技术设计可以确定 React 19 系列、React Flow 12 系列，而 patch 版本由实现阶段的 lockfile 和 Windows 冒烟测试锁定。不要在本轮无安装测试情况下宣布依赖组合验证通过。
