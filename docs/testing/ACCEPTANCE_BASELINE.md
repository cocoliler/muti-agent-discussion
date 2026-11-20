# 验收执行基线

- 基线日期：2026-11-18
- 团队提交：`6fa4b46b0baa617002bf9c8cfe1d119ed1af242d`
- 分支：`feature/f/T08-acceptance`

本文记录一次可复现的验收执行结果，不重新定义验收标准。验收操作和通过条件仍以
[通用验收标准](ACCEPTANCE_TESTS.md)为准，需求映射以
[需求追踪矩阵](../product/REQUIREMENTS_TRACEABILITY.md)为准，质量目标以
[质量属性](QUALITY_ATTRIBUTES.md)为准。

## 运行环境

| 项目 | 实际值 |
|---|---|
| 操作系统 | Windows |
| Python | 3.13.5（后端 `.venv`） |
| Node.js / npm | 26.13.0 / 11.20.0 |
| Git | 2.28.0.windows.1 |
| 浏览器 | 本机 Chrome，无头模式 |
| 后端数据库 | 独立临时 SQLite，Alembic 初始化 |
| 模式 | TEST；未执行 REAL 模型调用 |

## 自动化结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端 API、持久化、恢复、契约与 T04 | `python -m pytest backend/tests backend/tests_pm -q` | 197 通过，1 跳过 |
| T05 编排器 | `cd backend; python -m unittest discover -s tests -v` | 53 通过 |
| 契约产物漂移 | `python tech/scripts/export_contracts.py --check` | 25 个文件一致 |
| FastAPI 与操作表 | `python backend/tools/export_openapi.py` | 18 个操作一致 |
| 前端生产构建 | `npm.cmd run build` | 通过 |
| 前端领域测试 | `npm.cmd test` | 17 通过 |
| 浏览器独立回归 | `npm.cmd run test:browser` | 1 通过 |
| 浏览器与真实 HTTP 后端 | 设置 `FLOWSTUDIO_TEST_BACKEND_URL` 后运行浏览器回归 | 1 通过 |
| HTTP 端到端 TEST | `python scripts/e2e_smoke.py --base-url <测试后端>` | 8 个步骤通过 |

唯一跳过项是 `GuardedRealProviderTest.test_explicit_deepseek_validation_has_a_technical_candidate`。
该测试只有显式设置 `FLOWSTUDIO_PM_ENABLE_REAL_VALIDATION=1` 才会发起真实请求；本次没有真实模型证据。

## 验收状态

状态只表示本次执行覆盖程度，正式“通过”仍需按原验收文档完成证据和人工复核。

| 验收 | 当前覆盖 | 剩余动作 |
|---|---|---|
| A01 | 项目隔离 API 用例通过 | 纳入跨模块正式记录 |
| A02 | 规格版本 API 与浏览器导入导出一致性通过 | 汇总前端、API 和版本证据 |
| A03 | 规格结构与规划前置阻断用例通过 | 补模型调用次数为 0 的正式证据 |
| A04 | REAL 测试按设计跳过 | 阻塞：真实规格、模型配置、Prompt/模型版本和证据 |
| A05 | 结构、引用和需求覆盖自动化通过 | 补 must 需求“任务→交付→验收”人工评审 |
| A06 | 重复 ID、依赖、环和成果引用测试通过 | 汇总诊断位置与修复建议证据 |
| A07 | 菱形批次、随机 DAG、确定性测试通过 | 补同图不同坐标的正式对比记录 |
| A08 | 浏览器列表/画布/详情回归通过 | 保存截图与同源联调日志 |
| A09 | CAS、幂等、版本历史 API 测试通过 | 补浏览器 409 冲突与本地草稿保留证据 |
| A10 | 恢复、导出、本地历史测试通过 | 补实际进程重启、断网读取和导出比对 |
| A11 | 尚无用户规格变异执行 | 阻塞：依赖 A04；记录改动前后语义差异 |
| A12 | Provider 超时、401、空白、截断和预算测试通过 | 统一生成故障注入报告，确认无 TEST 降级 |

## 质量目标状态

| 目标 | 当前状态 | 说明 |
|---|---|---|
| Q01 | 自动化通过 | 契约 25 文件无漂移，OpenAPI 18 操作一致，前端构建通过 |
| Q02 | 未测量 | 尚无 100 任务/1000 边的正式性能报告 |
| Q03 | 未测量 | 功能与事务测试通过，尚无可复现 p95 结果 |
| Q04 | 未测量 | 浏览器功能回归通过，尚无帧率和首屏指标 |
| Q05 | 部分验证 | 恢复集成用例通过；实际杀进程重启仍待正式记录 |
| Q06 | 部分验证 | 调用次数、截止时间和错误边界测试通过；真实时延待 REAL |
| Q07 | 部分验证 | Host/Origin 和敏感内容用例通过；bundle/API/日志扫描待补 |
| Q08 | 未验证 | 键盘、对比度和窄屏的人工验收待执行 |

## 环境说明

本次完整后端合跑使用了独立 pytest 临时目录，因为当前宿主的全局
`pytest-of-<user>` 目录存在访问权限冲突。该处理不改变测试用例，只避开宿主环境问题。

同源浏览器联调需要把实际访问来源加入 `FLOWSTUDIO_ALLOWED_ORIGINS`。使用默认端口
`8000` 时已有预置来源；使用其他端口进行测试时必须显式配置，否则带 `crossorigin`
的生产脚本和样式会被 Origin 校验拒绝。

## 已发现的文档漂移

| 文档 | 旧描述 | 本次基线 |
|---|---|---|
| `docs/testing/TEST_STRATEGY.md` | pytest 119 项 | 不含 T04 的 `backend/tests` 为 120 项 |
| `docs/development/TASK_BREAKDOWN.md` | pytest 118 项 | 同上 |
| `tech/docs/system-architecture.md` 状态表 | API、事务、幂等和保存待实现 | 路由、API、SQLite 保存和契约漂移测试均通过 |
| `docs/README.md`、`DESIGN_SUMMARY.md` | 正式规划闭环待联调 | TEST、真实 HTTP 和浏览器同源联调已可运行 |

这些漂移不影响测试执行，但会让后续负责人基于过时状态排任务。状态修正应在独立的小 PR
中完成，不能与验收基线记录混在一起。

## 结论

团队提交的自动化技术基线为绿色。当前不能宣称 A01–A12 全部通过，主要缺口是 REAL
验收、人工语义评审、A09/A10 的正式跨模块证据以及 Q02–Q08 的测量记录。
