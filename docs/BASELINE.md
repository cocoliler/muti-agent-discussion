# FlowStudio 设计基线 v3.1

更新于 2026-11-18。仓库当前交付统一契约、浏览器工作台、服务端规划与计划版本，并完成 P0、REAL、人工语义评审和 Q01–Q08 质量验收。实现和验证入口见[实现审计](PROJECT_AUDIT.md)。

FlowStudio 是通用可定制的智能软件工程工作流平台（Configurable AI-Powered Software Engineering Workflow Platform）。课堂作业 2 的完整目标是 **Design Spec → PM Agent → Task Decomposition → Orchestrator → Workflow Visualization**。产品后续扩展到实际执行、监督、验证和反馈。

## 文档与代码的分工

- 产品范围和验收见[阶段边界](product/MVP_SCOPE.md)、[SRS](product/REQUIREMENTS.md)和[追踪矩阵](product/REQUIREMENTS_TRACEABILITY.md)。
- 字段、枚举和 API 以 [Pydantic 模型](../tech/flowstudio_contracts/)和其[生成产物](../tech/artifacts/README.md)为准。架构解释以[技术版系统架构](../tech/docs/system-architecture.md)为入口。原设计专题用于说明行为与取舍。
- 界面和浏览器流程见 [UI/UX](design/UI_UX_DESIGN.md)；当前行为由[实现审计](PROJECT_AUDIT.md)记录。历史归档见[文档入口](README.md)。

## 当前与目标

| 范围 | 当前可用 | 后续方向 |
|---|---|---|
| 规格和任务 | 结构化规格、手动任务、JSON 导入导出、示例项目、不可变 SpecRevision；PM Agent 支持 REAL 与显式 TEST | 扩展规划质量评估与产品案例 |
| 校验和编排 | 浏览器支持草稿预检；服务端编排通过 API 生成 PlanDerivation 和 ValidationReport | 扩展策略和回归样例覆盖 |
| 可视化和保存 | React Flow 编辑、本地草稿与快照、不可变 PlanRevision、独立 Layout、版本冲突提示和完整计划包 | 扩展计划与成果管理 |
| 执行与监督 | 画布表达任务计划、依赖和建议角色 | P2 增加实际任务执行、成果、审查与监督；Run 和 PlanningRun 各自维护状态 |

前端保留 React、TypeScript、Vite 和 React Flow。目标部署为 Windows 本地单体，后端选用 FastAPI、SQLite/SQLAlchemy，规划用 REST 轮询。PlanningPolicy 是可选约束。多 Agent 实际执行、LangGraph/SSE 等留待 P2 评估。
