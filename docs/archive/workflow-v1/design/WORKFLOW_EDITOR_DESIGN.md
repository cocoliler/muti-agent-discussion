# 工作流编辑器详细设计

依据 [React Flow 研究](../research/evidence/REACT_FLOW.md) 与 [统一基线](../BASELINE.md)。React Flow 负责图形交互；业务校验、撤销历史、保存协议、执行和版本均由 FlowStudio 实现，不依赖 Pro 示例源码。

## 1. 编辑器区域与工作模式

1920桌面：56px全局导航、240px节点面板、360px属性面板、56px顶栏。节点面板可收起，属性面板320–480px可调。1366桌面：48px导航，节点库默认覆盖式抽屉208px，属性面板320px。底部工具条40px；问题/日志抽屉默认关闭，展开224px（768高时180px）。不要同时永久展开多个横向占位侧栏。

顶栏从左到右：面包屑和流程名 → 草稿/版本标识 → 保存状态 → 校验 → 发布 → 试运行。发布不是“保存”替代品；已发布后继续编辑仍保存为草稿。流程名称过长截断，但可悬停/键盘聚焦读全名。

- **编辑草稿**：可以修改定义和布局，使用revision保存。
- **查看版本**：明确只读版本号，提供“返回草稿”；不在此偷偷更改草稿。
- **监控运行**：只读该run快照，属性面板替换为运行详情；不能拖动历史节点冒充原布局。

## 2. 节点视觉规范

默认左入右出、从左到右阅读。端口ID属于业务契约，端口中文label属于展示。卡片标题最多两行，完整名称面板展示；类型图标20px，标题13px/600，摘要12px，内边距12px，圆角8px。

| 节点 | 默认宽×最小高 | 标题/摘要 | 端口和特殊信息 |
|---|---|---|---|
| Start 开始 | 184×72 | 开始 / 输入schema名称 | 无控制入边，1个控制出端口；数据输入来源run.inputs |
| Transform 转换任务 | 224×96 | 用户名称 / 字段映射数量 | 1控制入、1控制出；输入绑定在面板，不在卡片显示全部JSON |
| Template 模板任务 | 224×96 | 用户名称 / 模板首行摘要 | 1入1出；显示引用数量与未解析提示 |
| Condition 条件 | 224×116 | 用户名称 / 条件短句 | 1入，true/false独立标记端口；不使用无法容纳文字的大菱形 |
| Parallel 并行 | 184×80 | 并行分发 / N条支路 | 1入，多出；激活所有出口，无“并发魔法”独立状态 |
| Join 汇聚 | 224×96 | 汇聚 / 等待已激活支路 | 多入1出；状态细节显示已解析/总入边和active数量 |
| Approval 人工审批 | 224×104 | 用户名称 / 审批提示摘要 | 1入1出；运行态显示截止时间，操作放详情面板 |
| Assert 验证 | 224×96 | 用户名称 / 断言数 | 1入1出；失败展示规则与实际值入口 |
| End 结束 | 184×72 | 结束 / 输出映射摘要 | 1入，无出；多支路必须先Join |

选择外圈2px蓝色，状态边框1px保留；字段错误右上红色感叹号+“2项问题”文字；hover不改变节点位置。类型小图标可用蓝/紫/灰作辅助识别，但运行状态色独立。分组是低饱和灰蓝底、1px虚线、标题栏24px、内边距24px，最多一层，不具有端口和执行器。

边：默认1.5px中性线；选中2px蓝线；错误2px红色+错误标记；已激活边2px蓝/绿并显示路径状态；INACTIVE灰色虚线并标“未选择”；UNRESOLVED保留中性。颜色必须配标签/线型。标签至少12px，点击命中区域增宽但不改变实际路径宽度。

## 3. 属性面板与动态表单

顶端显示类型/名称/节点ID复制入口；页签“配置 / 输入输出 / 执行策略”。配置由NodeDefinition的JSON Schema和受限UI提示生成，支持string/number/boolean/enum/object/array，深层JSON有文本编辑模式；不允许任意schema远程组件脚本。

输入字段选择“常量 / 引用”。引用选择器先列控制上游可用输出，显示类型和路径，不要求用户手敲表达式；生成 `{nodeId,port,pointer}` 结构。可能未执行的来源有“可跳过”标识，要求可选+fallback或改用Join.activeInputs。选择无效引用保留原值并报错，不能悄悄清空。

重试区显示“总尝试次数（含首次）”1–3、超时，纯节点允许重试，执行器声明不支持重试时禁用并说明。Approval显示截止期限，不显示普通自动重试。面板底部关联TaskItem，点击定位规格任务。

表单字段本地未提交文本归form state；失焦/确认且schema通过时形成editor command。无效文本留在面板，顶部显示“有未完成配置”；保存按钮先触发字段校验，阻止将无效文本丢失。取消编辑回退该字段最后有效值需明确操作。

## 4. 编辑命令与快捷键

| 命令 | 输入方式 | 原子性/反馈 |
|---|---|---|
| 新增 | 拖入节点库、点击节点类型后在视口中心添加 | 分配新UUID；必填字段提示；一次历史 |
| 连线/断线 | 拖端口；键盘连接表单；选边删除 | 即时方向/重复/自环/环/端口校验；后端发布再校验 |
| 拖拽/多选 | 指针；Shift框选；Ctrl点选 | dragStart记录起点，dragStop一次历史，过程不逐帧autosave |
| 复制粘贴 | Ctrl+C/V、Ctrl+D复制 | 新节点/边ID，重写选区内部引用；外部引用保留并显式提示；不复制run状态 |
| 删除 | Delete/Backspace；菜单 | 先列关联边和引用；确认批量删除一次历史；破坏引用产生校验项不自动猜测替换 |
| 分组/解组 | Ctrl+G / Ctrl+Shift+G | 转换相对坐标；保持绝对位置；组删除默认解组，删除内容需单独选择 |
| 撤销/重做 | Ctrl+Z / Ctrl+Shift+Z | 最多100条或20MiB，以先达到为限；拖拽/批量操作/一次表单提交合并 |
| 保存 | Ctrl+S | 提交有效表单并flush保存队列；服务成功后才显示已保存 |
| 自动布局 | 工具条；选择全图/选区 | 预览差异，应用后一次历史；不变更业务边和ID |
| 缩放/平移 | 滚轮、控件、Space拖动 | zoom 0.25–2；适应全图/所选；视口变化不污染业务撤销栈 |
| 搜索定位 | Ctrl+F（画布焦点时） | 按节点名/任务名找节点，选中并居中 |

表单、JSON编辑器内快捷键遵守文本编辑，不触发画布删除/复制。Esc关闭当前浮层/退出连接，不取消运行。键盘可用节点列表选择节点，用连接表单完成连线。

## 5. 分组与自动布局边界

P0采用单层视觉组及两级Dagre：测量实际节点尺寸 → 组内布局 → 组折叠为超节点 → 组间布局 → 恢复相对坐标。没有分组时直接布局平面DAG。

任意视觉分组可能使商图出现环，即使原执行图为DAG。例如A组n1→B组n2→A组n3。自动布局前检测商图；检测失败保留原布局，解释“跨组依赖无法按当前分组两级排列”，提供显式“展开为平面布局”选项：在平面布局执行节点后按成员重新计算分组包围框，保留组成员关系；若组框重叠，展示说明供手工整理，不伪造无重叠承诺。用户取消则不产生编辑命令。

React Flow官方指出Dagre跨子组边布局有限制；上述是FlowStudio拟实现策略，不是框架内建能力。ELK复合布局留到P1按实际复杂图需求评估。[官方比较](https://reactflow.dev/learn/layouting/layouting)

## 6. 组件树和模块目录

```text
apps/web/src/features/workflow-editor/
  WorkflowEditorPage
    EditorHeader / SaveStatus / PublishDialog / RunInputDrawer
    NodeLibrary / NodeSearch
    EditorCanvas (ReactFlowProvider + controlled ReactFlow)
      NodeRenderer -> NodeCard / PortHandle / NodeStatus
      WorkflowEdge / GroupFrame / MiniMap / CanvasToolbar
      SelectionToolbar / ConnectForm / ContextMenu
    InspectorPanel
      SchemaForm / BindingPicker / RetryPolicyForm / TaskLink
    ProblemsDrawer / ValidationIssueList
    NavigationLeaveGuard / ConflictDialog

apps/web/src/features/runs/
  RunPage -> ReadonlyRunCanvas / NodeRunInspector / ApprovalCard
             EventTimeline / AssertionDiff / ConnectionBanner
packages/contracts        API DTO与运行事件schema
packages/workflow-core    纯定义类型/校验/计划语义（不能导入React）
packages/node-catalog     可序列化节点元数据与服务端执行器注册
packages/test-fixtures    有效/无效图与期望行为
```

前端仅导入安全元数据入口，打包不得带服务端执行器、文件系统或数据库依赖。UI配置不保存React组件名，只保存业务type+version。

## 7. 状态所有权与保存协议

| 状态 | 唯一所有者 | 不应进入的地方 |
|---|---|---|
| 当前可编辑definition/layout/spec/tasks | 每个workflow一个Zustand editor store | TanStack Query实时覆盖、第二份全局nodes |
| 选区、悬停、打开面板、视口 | editor UI slice；视口本地偏好 | 执行定义/服务端运行状态 |
| 表单未提交文本/错误 | 受控form state | 未校验直接污染有效配置 |
| 项目列表、服务端版本、run快照 | TanStack Query | editor dirty内容的权威来源 |
| 运行事件投影 | 按runId的独立run store/query cache | WorkflowDraft nodes.data |
| 撤销栈 | editor command/history | 服务端历史、跨会话恢复承诺 |

首次加载query数据初始化editor一次；后续query刷新仅提示远程revision变化，不自动覆盖dirty state。React Flow nodes/edges是editor store内容经适配派生的受控视图，其内部状态不作为持久化真相。

保存队列规则：800ms防抖、同workflow同时最多一个请求；捕获提交快照和`baseRevision`。保存期间的新修改继续留在editor store。成功仅将已提交的edit sequence标记为已保存，不能把其后编辑一起标clean。服务器返回newRevision，剩余修改下一次基于此revision提交。保存失败保留dirty内容，重试不得用过期revision盲覆盖。

409冲突：停止autosave，展示“此流程在另一标签页已修改”；提供导出本地未保存JSON、重新加载服务器版本、另存副本（P1）说明。P0只提供导出与重新加载，不自动merge、不覆盖远程。即使单用户仍可能有双标签页冲突。

发布/试运行：先提交有效表单、flush保存、获得确切revision；请求带expectedRevision。服务端同事务验证revision并冻结definition/layout/spec/tasks，试运行也有快照。保存或校验失败时不进入运行，不依据前端当前画布自行构造运行色彩。

## 8. 校验与运行投影

连接即时校验仅作反馈；完整静态校验包含required、连接、不可达、DAG、端口类型、参数引用/支配关系、Condition出口、任务依赖映射。结果具有code、severity、message、nodeId/edgeId、fieldPath、fixHint、validatedRevision。点击问题居中节点并展开对应字段；过期问题显示标识并建议重新校验。

运行渲染由快照布局与nodeId关联的NodeRun投影组成。SSE事件按seq去重，缺口重新取快照/补发；浏览器断线不改变后端运行状态。尝试耗时和总耗时分开；SKIPPED显示路径未激活，FAILED显示实际错误，INTERRUPTED显示服务中断。不按前端拓扑顺序播放假执行动画。
