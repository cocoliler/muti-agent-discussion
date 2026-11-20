# P0 技术闭环报告

报告日期：2026-11-18

验收基线：`1898a4fb84846ea457b9f3fb679653a331cd3831`

本文记录阶段3的跨模块 P0 技术闭环结果。验收判据仍以
[通用验收标准](ACCEPTANCE_TESTS.md)为准；本文不新增或改写标准。

阶段4的真实模型结果已更新 A04、A05、A11，详见
[REAL 验收报告](REAL_ACCEPTANCE_REPORT.md)。

## 执行范围

本次运行由 `scripts/verify_all.ps1` 自动完成：

- 197 项后端测试通过，1 项 REAL 测试按设计跳过
- T05 编排器 53 项通过
- 契约 25 个文件无漂移，FastAPI 18 个操作与操作表一致
- 前端生产构建、17 项领域测试和浏览器回归通过
- 浏览器与真实 HTTP 后端联调通过
- TEST HTTP 全链路通过
- 新增 P0 HTTP 验收覆盖项目隔离、规格版本、错误规格、坏图、菱形编排、布局独立、并发冲突和导出
- 实际停止并重启后端后，规格、计划、布局和导出 hash 保持一致

P0 HTTP 验收脚本位于 `backend/scripts/p0_acceptance.py`，已接入统一验收入口。

## P0 状态

| 验收 | 状态 | 本次证据 | 结论 |
|---|---|---|---|
| A01 | 技术通过 | P0 HTTP：双项目规格隔离、跨项目引用拒绝 | 数据未串用 |
| A02 | 技术通过 | P0 HTTP：规格 v1/v2 独立读取，旧 hash 不变 | 版本不可变 |
| A03 | 技术通过 | P0 HTTP：重复需求 ID 被拒绝且不新增版本；后端测试证明模型前阻断 | 无静默编造 |
| A04 | 技术通过 | 阶段4 REAL baseline 成功；模型、usage、attempt 和候选均已记录 | 成功响应仍不暴露 Provider request ID |
| A05 | 通过 | 结构、引用和需求覆盖测试通过；F 技术审查通过；朱志伟于 2026-11-18 完成非模块作者人工签字 | must 需求语义覆盖正式通过 |
| A06 | 技术通过 | 重复 ID、缺依赖、自环、环、缺成果引用均被拒绝 | 诊断码与空派生结果正确 |
| A07 | 技术通过 | 菱形批次为 `[[A],[B,C],[D]]`；布局更新后 hash、批次和顺序不变 | 坐标不影响业务顺序 |
| A08 | 技术通过 | 浏览器列表/画布/详情回归通过；规格、工作流、深色截图已生成 | 需在交付时引用截图 |
| A09 | 技术通过 | 计划 v2 追加成功，旧 expected_revision 返回 409，v1 hash 和内容不变 | 冲突不覆盖历史 |
| A10 | 技术通过 | 实际停止并重启后端后，规格/计划/布局/导出均保持 | 本地历史可恢复 |
| A11 | 技术通过 | 阶段4 R01 需求变化和 standard→coarse 均产生有意义差异 | 无 |
| A12 | 技术通过 | Provider 401、超时、空白、截断、预算和重试测试均在统一日志中通过 | 无静默 TEST 降级 |

当前 12 项 P0 已全部完成技术闭环，A05 的非模块作者人工签字也已完成。因此当前结论是：

**P0 技术链路、REAL 验收与人工语义评审全部通过，正式 P0 关闭。**

## 证据入口

最近一次完整运行的本地证据位于：

```text
evidence/runs/20261118-145823/
  summary.md
  summary.json
  logs/p0-http-acceptance.log
  logs/restart-verify.log
  logs/browser-standalone.log
  logs/browser-http.log
  logs/backend-pytest.log
  browser-spec.png
  browser-workflow.png
  browser-dark.png
  p0-state.json
```

该目录包含临时数据库和运行证据，按 `evidence/.gitignore` 保留在本地，不提交 Git。

## 后续增强

如交付要求逐次供应商 request ID，扩展 PlanningRun 审计或归档 T04 内部证据。
该项不阻塞当前 P0 关闭。

本次验收未发现产品功能缺陷；发现并修正的是验收脚本对业务错误码所在层级的错误假设。
