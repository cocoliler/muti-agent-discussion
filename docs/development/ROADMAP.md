# 分阶段开发路线
阶段按依赖推进，不预估未经团队确认的日历工期。P0先完成，P1不得阻塞课堂闭环。表中列出阶段目标，当前完成情况见[实现审计](../PROJECT_AUDIT.md)。

| Phase | 目标与具体任务 | 依赖/接口 | 交付及验收/测试 | 分工 | 风险与应对 |
|---|---|---|---|---|---|
| 0 审计研究 | 核对main、源码与参考项目 | 无 | 审计/来源/未验证项；核对实际文件 | A/F | 参考漂移→记录范围与版本 |
| 1 需求架构 | T01冻结Schema、API、规划参数、错误；Windows兼容实验 | tech/flowstudio_contracts | 生成Pydantic/OpenAPI/TS，无差异；Schema校验 | A，各owner Review | 字段冲突→契约PR先行 |
| 2 规格管理 | T02项目、规格、SQLite；T03表单/JSON | SpecRevision | A01–A03；离线保存和重启 | E/B | 输入缺失→阻止模型调用 |
| 3 PM规划 | T04 Provider/预算/Prompt/候选 | TaskProposal | A04–A05/A12；REAL与故障注入 | C/E | 模型不稳定→有界修复和证据 |
| 4 编排 | T05纯验证、Kahn、阶段、输入引用 | build_plan | A06–A07；随机DAG与坏图 | D | 批次误算→冻结每轮frontier |
| 5 可视化 | T06列表/画布同源、详情/诊断 | DTO/Report/Layout | A08；键盘和截图检查 | B | 重绘/陈旧响应→稳定ID/hash |
| 6 集成验收 | T07版本/API、T08通用流程测试 | 前五阶段 | A01–A12全通过；记录REAL结果与人工评分 | E/B/F/C | 固定模板假通用→变异测试 |
| 7 Agent扩展 | T10角色注册/ExecutionBinding/成果 | 冻结PlanRevision | A15；受限工具真实交付、输入hash | C/D/E | 工具副作用→隔离和授权 |
| 8 监督验证 | T11运行事件、审批、Reviewer/新Run修订 | Run/Artifact/Event | A16；失败/取消/迟到结果/审批 | D/E/B/F | 恢复冲突→事件与业务对账 |
| 9 完善 | T09体验优化、T12文档输入、性能与恢复 | 度量与用户反馈 | A13–A14/A17、Q01–Q08；回归与备份恢复 | 全员/F | 范围再次膨胀→按证据排序 |

T01、T02、T03、T04、T05、T06、T07 与 T08 的 P0 范围已交付并形成证据；A01–A12、REAL/变异、人工语义评审和 Q01–Q08 均已完成。P2 的 T10–T12 仍为后续范围。
