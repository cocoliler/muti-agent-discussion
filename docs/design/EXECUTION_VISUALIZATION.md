# 当前规划与未来执行可视化

当前前端显示任务、依赖、拓扑批次、未覆盖需求提示、建议角色和规划问题。界面上的「编排有效」表示浏览器当前任务通过本地试排。后续接入服务端后，PlanningRun 的 SUCCEEDED 表示规划请求完成，计划是否正式保存由 PlanRevision 表达。

P2 新增运行选择器，读取固定 PlanRevision 并叠加 TaskRun.status。任务详情新增 Attempt、输入 manifest、Artifact 和审查证据。计划依赖保持静态；真实交接来自 Message/Event，放在可切换的图层。

运行事件按 Run 隔离，迟到结果只进入对应运行的状态。SSE 断线显示连接中断，恢复后补齐或重读快照；实际运行失败独立展示。终态可导出证据报告。
