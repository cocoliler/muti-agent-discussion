# 需求追踪矩阵
需求描述唯一来源为 [SRS](REQUIREMENTS.md)。实现状态以 [审计](../PROJECT_AUDIT.md) 为准。表中路径为设计文档，不代表业务代码已存在。

| 需求 | 权威设计 | 开发任务/负责人 | 验收 |
|---|---|---|---|
| R01 | architecture/DATABASE_DESIGN.md | T02 E | A01 |
| R02–R03 | architecture/DESIGN_SPEC_MODEL.md | T01 A、T02 E、T03 B | A02–A03 |
| R04–R05 | architecture/PM_AGENT_DESIGN.md、TASK_DECOMPOSITION.md | T04 C | A04–A05 |
| R06–R07 | architecture/ORCHESTRATION_ENGINE.md、WORKFLOW_VALIDATION.md | T05 D | A06–A07 |
| R08 | design/WORKFLOW_EDITOR.md | T06 B | A08 |
| R09–R10 | architecture/API_SPECIFICATION.md、DATABASE_DESIGN.md | T07 E/B | A09–A10 |
| R11–R12 | testing/TEST_STRATEGY.md、architecture/PM_AGENT_DESIGN.md | T08 F/C | A11–A12 |
| R13–R14 | design/WORKFLOW_EDITOR.md、architecture/DESIGN_SPEC_MODEL.md | T09 B/A | A13–A14 |
| R15–R16 | architecture/SE_WORKFLOW_EXTENSION.md | T10 D/C/E、T11 F/B | A15–A16 |
| R17 | architecture/DESIGN_SPEC_MODEL.md | T12 C/E | A17 |
| R18 | product/MVP_SCOPE.md | 无 | 范围检查 |
| Q01–Q08 | testing/QUALITY_ATTRIBUTES.md | T08 F，各模块 owner | Q01–Q08 |

PR 必须列需求 ID、变更契约、测试编号及结果；契约变更同步本表。F 汇总证据，不代替各模块作者测试。
A01–A17 的操作与判据见 [验收](../testing/ACCEPTANCE_TESTS.md)，通用验证方法见 [测试策略](../testing/TEST_STRATEGY.md)。
