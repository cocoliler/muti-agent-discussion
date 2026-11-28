# 阶段 2.4 工作草稿与专业审查

## 运行流程

1. Host 生成工作草稿 v1，并用 `discussion_targets` 标明本轮关注的条目 ID。旧输出缺少该字段时，系统从模块、流程、边界、假设、风险、待确认问题和约束中各取一个存在的条目，作为现有议题的默认关联。
2. `AgendaItem.draft_refs` 保存议题关联，`SpeakerTask.working_draft_id` 和既有输入快照约束任务。专业角色读取目标、与职责相关的草稿摘录、硬约束、相关假设与证据。同批独立任务沿用 `asyncio.gather`，最多并行三个。定向回应、评分和独立评审继续按阶段执行。
3. 专业角色在正常发言里附带条目修改建议。系统绑定实际作者、基准草稿和建议 ID。无新增意见时可以正常结束。重大或阻断建议须关联现有 Issue。需要用户确认的建议须关联价值分歧。质疑者检查已有结果，程序将同一条目的竞争性修改补到现有评分流程。
4. Host 在整合阶段逐项给出采纳、未采纳或暂缓及其理由。程序只应用无冲突的普通建议，保护硬约束、已确认内容和待确认问题。更新保留条目 ID。删除经引用和完整性检查，新增作为关联原条目的设计补充保存。结构检查失败时保留旧稿并记录失败。
5. 修订通过后，旧稿和新稿随讨论状态同事务保存，记录原版本、建议结果、消息依据、修改对象和未解决问题。工作台现有工作草稿卡片显示版本，修订明细可展开并跳回原发言。上一版可按原 ID 读取。
6. Host 基于最新工作草稿形成冻结候选稿，候选稿记录工作稿来源。独立 Reviewer 读取冻结候选稿，沿用已有评审和证据门禁。正式签收和规格导出使用候选稿及评审记录。

## 模拟示例

在会议室预约场景，Host 先保存 v1，议题关联预约入口模块、主流程和系统边界。Product 与 Architect 分别补充入口模块和边界说明，Designer 返回无新增意见。Host 采纳两条补充后保存 v2，模块 ID、约束和业务流程保持一致，随后进行独立评审。另一场景中两位角色修改同一模块，系统进入评分，竞争修改留在修订记录，原条目保留。若有管理权限偏好，系统等待用户确认，工作稿保持 v1。

## 参考与实现边界

- Magentic-One 用 Task Ledger 保存事实、任务和计划，Progress Ledger 决定进展、下一位执行者或重新规划。这里用 Agenda、Issue 和快照记录协调所需状态，使用已有阶段规则。 [AutoGen 编排实现](https://github.com/microsoft/autogen/blob/83afbf5857aac683340d4c692194e548b1e8edda/python/packages/autogen-agentchat/src/autogen_agentchat/teams/_group_chat/_magentic_one/_magentic_one_orchestrator.py) · [Magentic-One 官方说明](https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/magentic-one.html)
- BMad 允许已有 Product Brief 和研究馈入 PRD，并在后续加入 UX 和架构成果。这里将工作草稿作为已有共同成果，专业角色按职责补足局部。 [BMad 需求与规格](https://github.com/bmad-code-org/BMAD-METHOD/blob/bab9082c3e884e0d72c759bcd1a6d90bcc01be84/docs/plan/define-requirements-and-a-specification.md) · [BMad 路径与角色](https://github.com/bmad-code-org/BMAD-METHOD/blob/bab9082c3e884e0d72c759bcd1a6d90bcc01be84/docs/plan/choose-a-planning-path.md)
- Anthropic 区分独立子任务并行、协调者分工以及有明确标准的评估。这里复用已有并行调度和质量门禁，工作稿的检查与正式评审各有输入和责任人。 [Building effective agents](https://www.anthropic.com/research/building-effective-agents#building-blocks-workflows-and-agents)

当前合并粒度是已有条目的文字与关联设计补充。新增模块、修改流程步骤或重连依赖等结构化补丁留待后续。评分结果和事实核对继续写入现有讨论记录，竞争修改或待确认建议需要明确处理后才能反映到正文。全过程验证采用本地模拟，未测量真实模型的质量、用量与延迟。

## 代码位置与验证

- 条目索引、输入摘录、建议和确定性修订。 [collaboration.py](../../backend/initial_drafts/collaboration.py)
- Host 和专业角色契约、执行适配及版本快照。 [generation.py](../../backend/initial_drafts/generation.py) · [contracts.py](../../backend/roles/contracts.py) · [agent_execution.py](../../backend/discussions/agent_execution.py) · [models.py](../../discussion_orchestration/models.py) · [engine.py](../../discussion_orchestration/engine.py)
- 原子保存、草稿与 Issue API、前端修订记录。 [application.py](../../backend/discussions/application.py) · [repository.py](../../backend/discussions/repository.py) · [routes.py](../../backend/discussions/routes.py) · [schemas.py](../../backend/discussions/schemas.py) · [DiscussionCards.tsx](../../frontend/src/DiscussionCards.tsx) · [discussions.ts](../../frontend/src/discussions.ts)
- [端到端测试](../../tests/test_host_working_draft.py) 覆盖同版本并行、无新增意见、精确关联、保留原稿、修订审计、旧结果隔离、价值分歧、评分、非法删除和独立评审。

本地回归通过 272 项 Python 测试与 4 项前端测试。TypeScript 类型检查和 Vite 生产构建均通过。
