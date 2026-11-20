# 通用测试策略
测试分为已可运行的浏览器回归、契约产物检查和课堂闭环验收。自动化使用抽象图与统一契约样例，工作台的显式预设可用于人工演示。
实施时使用用户实际输入规格开展REAL验收；自动化测试使用最小抽象图和故障注入，不按产品名选择固定输出。

## 分层验证
| 层级 | 检查内容 | 证据 |
|---|---|---|
| Schema | 必填、未知字段、长度、枚举、引用形状 | 自动化断言及字段错误路径 |
| 图算法 | 链、分叉汇合、多根、独立组件、重复ID、缺引用、自环、环及环下游 | 每条边满足batch(source)<batch(target)；输出覆盖所有合法任务且唯一 |
| 持久化/API | 事务回滚、CAS、幂等键、版本不可变、跨项目引用拒绝 | 临时数据库集成测试和请求响应 |
| Provider | 空白、截断、非法JSON、429、401、超时、usage缺失 | 模拟传输与预算账本；真实模型测试独立 |
| UI | 列表/图/详情同源、错误定位、dirty/冲突/历史只读、快捷键 | 浏览器E2E、截图和键盘检查 |
| 通用性 | 改变实际输入中的一项需求、限制或粒度，观察计划响应 | 真实输入输出、需求映射及人工评审 |
| P2运行 | 成果来源、依赖满足、迟到回调、取消、审批和恢复 | Run/Attempt/Artifact/Event证据 |

## 语义评审
每项must需求由评审者检查：有对应任务（0/1）、交付可覆盖需求（0/1）、验收可观察（0/1）。must三项均为1才通过，不能用平均值掩盖遗漏。
任务粒度、重复、阶段与依赖合理性分别记录“通过/需改/不适用”及task_id和理由；存在关键遗漏/错误依赖即失败。
需求引用覆盖率只是结构指标；LLM自评分不能代替人工判断。评审者应至少一人非该模块作者。
改变需求后不要求全部ID变化，要求相关交付和验收发生有意义变化；不以节点数变化作为唯一通用性证据。

## 执行与报告
前端使用 `npm run build`、`npm test`、`npm run test:browser` 和可选的 `npm run test:performance`；浏览器测试使用本机 Chromium。
后端使用 `python -m pytest backend/tests backend/tests_pm`（当前 197 通过、1 项 REAL 守卫测试跳过）与 `cd backend; python -m unittest discover -s tests`（T05 编排器 53 项）；`python backend/tools/export_openapi.py` 检查路由与契约操作表的漂移。真实 HTTP 端到端自证由 `backend/scripts/e2e_smoke.py` 承担，P0 跨模块与进程重启由 `backend/scripts/p0_acceptance.py` 验证，REAL 由 `backend/scripts/real_acceptance.py` 单独执行。统一入口为 `scripts/verify_all.ps1`，质量测量加 `-Quality`。证据见[实现审计](../PROJECT_AUDIT.md)、[REAL 报告](REAL_ACCEPTANCE_REPORT.md)和[质量报告](QUALITY_ACCEPTANCE_REPORT.md)。
记录commit、环境、依赖、输入hash、模式、Prompt/规则版本、模型实际ID、usage、候选和验证报告、人工判定。
没有Key或外部模型失败时记录REAL阻断，TEST仍可测试结构但不能替代真实智能规划验收。
