# 通用设计摘要

FlowStudio 面向需求结构化、计划编排、开发执行、验证与反馈。课堂作业 2 聚焦从设计规格和 PM 生成的候选任务，到可校验、可修改、可冻结的开发计划。

## 当前能演示的流程

1. 创建空项目，填写结构化 Design Spec 或导入规格 JSON。演示时也可显式创建「会议室预约」项目。
2. 手动录入任务或导入 TaskProposal JSON。预览显示 JSON、诊断与可计算的任务和批次统计。
3. 重新编排。浏览器检查任务与成果引用、环、需求覆盖、假设和规模限制，然后按拓扑层计算并行批次。
4. 在列表和画布中使用同一组任务 ID。可搜索定位、编辑依赖、卡内编辑注释、自动布局、导出 PNG、保存草稿或记录只读本地快照。

## 正式规划链路的设计

- PM Agent 根据 SpecRevision 和可选 PlanningPolicy 生成 TaskProposal，以预算、有限重试和诊断约束模型调用。
- 服务端确定性编排器生成 PlanDerivation 和 ValidationReport，前端投影结果。坐标、视口和注释独立于业务顺序。
- 通过校验并完成假设确认后可保存不可变 PlanRevision，写入用 expected_revision 与幂等键处理冲突及重试。
- 任务规划、计划保存和实际执行分开。P2 再引入执行、成果验证与监督。

当前 T02/T07 的项目、规格、计划、布局和导出 API 已接入；T04 REAL、T05 服务端编排、浏览器联调、P0 验收及 Q01–Q08 测量均已完成。后续 P2 再引入实际执行、成果验证与监督。参见[实现审计](PROJECT_AUDIT.md)、[REAL 验收报告](testing/REAL_ACCEPTANCE_REPORT.md)、[质量报告](testing/QUALITY_ACCEPTANCE_REPORT.md)和[开发路线](development/ROADMAP.md)。
