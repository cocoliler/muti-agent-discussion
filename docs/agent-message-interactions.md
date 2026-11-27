# Agent 消息交互审计与实现

## 现有系统

- TW2 是 FlowStudio 的上游需求工作台。当前仓库包含 React 讨论界面、FastAPI 编排服务和 SQLite 持久化。FlowStudio 画布通过 `DesignSpecContent` JSON 导入签收规格，画布实现位于另一仓库。
- `discussion_messages` 提供稳定消息序号、回复关系、来源引用和 `after_seq` 分页。角色输出是经校验后一次提交的 JSON。旧客户端每 2.5 秒读取会话，再按序号读取新消息。
- `role_attempts` 已保存每次执行的状态、时间、耗时和错误类别。会话快照已有角色状态和待派发任务。原界面主要从参与者侧栏查看这些状态，发言、调度和交付入口缺少统一的展示层。
- 可复用的界面包括已有消息格式化、轮次导航、回复与修订输入框、评审意见、规格预览和签收导出。

## 现在的读路径

`GET /api/v1/discussions/{id}/events` 发布会话与任务尝试的变更通知。客户端使用原有会话接口和消息游标补齐状态，断开时继续增量轮询。变更通知不携带模型内容。`GET /api/v1/discussions/{id}/activity` 按游标分页，只包含执行元数据和错误类别，已写入讨论的成果可按任务关联打开。

| 展示类型 | 数据来源与组件 |
| --- | --- |
| Message | 持久化发言和面向用户的正文 |
| Thinking | 可以公开的发言分析说明和引用 |
| Progress | 会话及参与者真实状态，以及按角色和轮次合并的执行事件 |
| ToolCall | 持久化执行尝试、耗时、结果状态及错误类别 |
| Decision | 独立卡片展示持久化决策 |
| Artifact | 独立规格成果卡片，复用预览与签收导出 |
| Summary | 持久化整合发言及签收成果摘要 |

简洁、标准、详细模式在前端投影同一份记录。定位被简洁模式隐藏的来源时，可临时显示该条消息。面向用户的正文采用结论优先的写法，详细论证继续用于校验与追溯。

## 边界与验收

- 事件流的粒度是已记录的状态变更。模型正文在完整校验后入库，原始推理、提示词、第三方工具调用及其原始结果当前没有公开的数据源。
- 调用结果由执行器按批次合并，角色状态以持久化状态为准。模拟执行仅由独立模拟模式产生。
- 规格导出继续使用既有签收接口及 FlowStudio JSON 契约。验证覆盖事件流、执行记录权限范围、展示过滤、并行角色、失败、引用回跳和成果类型。

## 修改文件

- 展示类型与组件：`frontend/src/discussionTimeline.ts`、`DiscussionCards.tsx`、`DiscussionActivity.tsx`、`DiscussionMessageText.tsx`。
- 工作台接线和样式：`frontend/src/Workbench.tsx`、`DiscussionSpec.tsx`、`discussions.ts`、`workbench.css`。
- 事件和执行记录：`backend/discussions/routes.py`、`schemas.py`、`application.py`、`repository.py`。
- Agent 发言规则：`backend/roles/contracts.py`。
- 新增回归用例：`frontend/tests/discussionTimeline.test.mjs`、`tests/test_discussion_activity.py`。
