# 多 Agent 需求讨论工作台

这是 FlowStudio 的上游需求工作台。用户提交项目想法后，多个专业 Agent 在同一个项目群聊中讨论范围、约束和风险，记录员持续更新共识稿。独立评审检查需求依据与验收条件，用户签收通过评审的版本，生成可直接导入 FlowStudio 的结构化设计规格。

## 从想法到设计规格

1. 主持人整理背景、目标、用户和约束，请用户确认摘要或补充关键事实。
2. 产品、研究、架构、设计和商业分析角色并行发言。质疑者引用具体观点提出问题，相关角色定向回应。
3. 记录员将共识、分歧和风险整理为带版本的候选规格。每条需求可追溯到对应发言。
4. 独立评审给出通过、退回修订或需要补充的结论。修订保留需求 ID，并将新版本重新送审。
5. 用户签收通过评审的当前版本，导出 UTF-8 JSON 供 FlowStudio 导入。

## FlowStudio 交接

正式输出遵循 FlowStudio v3.1 的 `DesignSpecContent` 契约，`schema_version` 为 `"1.0"`。功能需求、非功能需求、优先级与验收条件使用现有字段。讨论消息、来源映射、风险和评审历史保存在工作台内，通过稳定需求 ID 关联。

首版以 JSON 导出和 FlowStudio 现有导入入口验收。后续可通过规格版本 API 创建正式 `SpecRevision`，交给 FlowStudio 的 PM Agent 继续任务规划和编排。 [规格 Schema](tech/artifacts/schema/design-spec.schema.json) · [交接设计](FLOWSTUDIO_DESIGN_INPUT.md#62-本地导入与-api-交接)

## 当前进度

当前提交交付设计文档与兼容契约。可运行工作台尚待实现。目标环境为 Windows 本地浏览器，计划采用 React、FastAPI 和 SQLite。

| 阶段 | 验收内容 |
| --- | --- |
| 模拟讨论 | 通过会议室预约案例验证角色发言、引用质疑、独立评审退回、定向修订和刷新恢复 |
| 真实模型 | 验证角色输出校验、人工澄清、调用预算、失败恢复和成本记录 |
| 规格交接 | 签收当前版本，验证 JSON 导入，再验证 API 幂等写入和版本冲突处理 |

## 设计文档

- [多 Agent 讨论与设计规格生成](FLOWSTUDIO_DESIGN_INPUT.md)
- [FlowStudio v3.1 基线](BASELINE.md)
- [设计规格模型](architecture/DESIGN_SPEC_MODEL.md)
- [FlowStudio REST API](architecture/API_SPECIFICATION.md)
