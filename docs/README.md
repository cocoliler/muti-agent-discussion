# FlowStudio 通用设计文档 v3.1

FlowStudio 是通用可定制的智能软件工程工作流平台。当前 P0 闭环、REAL 验收、人工语义评审和 Q01–Q08 质量测量均已完成；P2 执行与监督仍为后续范围。分支状态、可运行范围和测试证据见[实现审计](PROJECT_AUDIT.md)。

优先阅读：[设计摘要](DESIGN_SUMMARY.md) → [基线](BASELINE.md) → [技术版系统架构](../tech/docs/system-architecture.md) → [评审](DESIGN_REVIEW.md)。

## 完整设计目录

### 产品与需求

- [范围与里程碑](product/MVP_SCOPE.md)
- [产品需求文档](product/PRD.md)
- [产品愿景与定位](product/PRODUCT_POSITIONING.md)
- [软件需求规格说明书（SRS）](product/REQUIREMENTS.md)
- [需求追踪矩阵](product/REQUIREMENTS_TRACEABILITY.md)

### 项目研究

- [交互设计对照](research/DESIGN_BENCHMARK.md)
- [参考项目研究与设计取舍](research/REFERENCE_PROJECTS.md)

### 系统架构与核心流程

- [Agent 通信（P2）](architecture/AGENT_COMMUNICATION.md)
- [REST API 契约](architecture/API_SPECIFICATION.md)
- [架构决策记录](architecture/ARCHITECTURE_DECISIONS.md)
- [数据库设计](architecture/DATABASE_DESIGN.md)
- [Design Spec 与通用规划参数](architecture/DESIGN_SPEC_MODEL.md)
- [模块详细设计](architecture/MODULE_DESIGN.md)
- [Agent 职责与扩展](architecture/MULTI_AGENT_ARCHITECTURE.md)
- [确定性编排器](architecture/ORCHESTRATION_ENGINE.md)
- [PM Agent 详细设计](architecture/PM_AGENT_DESIGN.md)
- [最终 SE Workflow 扩展架构（P2）](architecture/SE_WORKFLOW_EXTENSION.md)
- [状态与恢复](architecture/STATE_MANAGEMENT.md)
- [系统架构](architecture/SYSTEM_ARCHITECTURE.md)
- [任务分解数据与质量控制](architecture/TASK_DECOMPOSITION.md)
- [技术选型与决策依据](architecture/TECH_STACK.md)
- [领域模型与工作流 Schema](architecture/WORKFLOW_DATA_MODEL.md)
- [编排验证与错误协议](architecture/WORKFLOW_VALIDATION.md)

### UI/UX

- [设计系统](design/DESIGN_SYSTEM.md)
- [当前规划与未来执行可视化](design/EXECUTION_VISUALIZATION.md)
- [UI/UX、信息架构与用户旅程](design/UI_UX_DESIGN.md)
- [工作流编辑器](design/WORKFLOW_EDITOR.md)

### 工程实施

- [Git 协作规范](development/GIT_COLLABORATION.md)
- [Windows 本地开发与运行](development/LOCAL_DEPLOYMENT.md)
- [工程组织与契约管理](development/PROJECT_STRUCTURE.md)
- [分阶段开发路线](development/ROADMAP.md)
- [可执行开发任务](development/TASK_BREAKDOWN.md)
- [团队分工](development/TEAM_ASSIGNMENTS.md)

### 测试与验证

- [验收执行基线](testing/ACCEPTANCE_BASELINE.md)
- [P0 技术闭环报告](testing/P0_CLOSURE_REPORT.md)
- [REAL 验收报告](testing/REAL_ACCEPTANCE_REPORT.md)
- [质量属性验收报告](testing/QUALITY_ACCEPTANCE_REPORT.md)
- [通用验收标准](testing/ACCEPTANCE_TESTS.md)
- [质量属性与测量目标](testing/QUALITY_ATTRIBUTES.md)
- [通用测试策略](testing/TEST_STRATEGY.md)

### 契约附件与总体评审

- [生成的 Design Spec JSON Schema](../tech/artifacts/schema/design-spec.schema.json)
- [生成的 TaskProposal JSON Schema](../tech/artifacts/schema/task-proposal.schema.json)
- [OpenAPI](../tech/artifacts/openapi.json)
- [实现审计](PROJECT_AUDIT.md)
- [总体评审](DESIGN_REVIEW.md)
- [设计摘要](DESIGN_SUMMARY.md)

## 与所需成果的对应

保留已有目录以免重复维护：PRODUCT_VISION对应PRODUCT_POSITIONING；SRS对应REQUIREMENTS；SCOPE_AND_MILESTONES对应MVP_SCOPE；DOMAIN_MODEL/WORKFLOW_SCHEMA合并于WORKFLOW_DATA_MODEL；TECHNOLOGY_EVALUATION对应TECH_STACK。信息架构、组件树和用户旅程合并于UI_UX_DESIGN；测试方法与验收分别在TEST_STRATEGY和ACCEPTANCE_TESTS；详细图算法在ORCHESTRATION_ENGINE。

历史 [workflow-v1](archive/workflow-v1/BASELINE.md) 和原 `docs/contracts/` Schema 附件供追溯。正式字段、JSON 格式和接口见 `tech/` 生成产物。
