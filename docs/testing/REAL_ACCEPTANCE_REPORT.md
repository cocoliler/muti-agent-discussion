# REAL 验收报告

报告日期：2026-11-18

验收基线：`74cf212`

输入：用户提供的跨园区会议室预约与访客协同规格

规格 hash：

- baseline：`66912432ab4f35b338e2b8c06a582722bd920a796879974195206f3ceae60691`
- 单项需求变化：`a4d8be4d88f25febaaa518d925863a83d9d4dfa334fe7672eff1d3bcfaa2e78c`

## 配置

| 项目 | 实际值 |
|---|---|
| Provider | DeepSeek |
| 模型 | `deepseek-flash` |
| Prompt | `pm-v1` |
| 规则 | `1.0` |
| baseline 粒度 | `standard` |
| 变异粒度 | `coarse` |
| 模式 | REAL，无 fixture |

## 真实执行

| 运行 | 结果 | 任务数 | 尝试次数 | 耗时 | Token usage |
|---|---|---:|---:|---:|---|
| baseline standard | SUCCEEDED | 13 | 2 | 32.210 秒 | 16,260 |
| R01 单项需求变化 | SUCCEEDED | 13 | 2 | 40.221 秒 | 16,998 |
| standard → coarse | SUCCEEDED | 12 | 1 | 20.109 秒 | 6,959 |

基线候选通过 Schema、图校验和 must 需求引用检查。没有阻断问题或 critical 假设；
7 个假设均为 low。真实调用均不超过每次 60 秒、总计 180 秒和最多 3 次调用边界。

R01 变化后，引用 R01 的 T01、T02、T10 内容均发生有意义变化。粒度从 standard
变为 coarse 后，任务数从 13 变为 12，全部 13 个任务指纹发生变化；这证明计划并非固定表，
而是对输入和粒度产生响应。

## 可靠性观察

真实模型存在非确定性，验收不要求两次生成字节一致：

- 另一个独立 standard baseline 生成了 11 个任务的合法候选，说明任务数量不是质量判据。
- 一次重复 baseline 在 2 次调用后返回 `MODEL_BUDGET`，usage 为 18,145；系统有界失败，
  没有自动切换 TEST，也没有继续无限调用。
- 一次较早的需求变化触发 critical assumption，系统返回 `NEEDS_INPUT`；后续聚焦变异成功，
  说明关键缺失会进入补充信息流程，而不是静默编造。

## A05 语义评审

本次由 F 完成非模块作者技术审查，结论为“建议通过”；非模块作者朱志伟已于
2026-11-18 完成人工签字，A05 正式通过。

| must 需求 | 对应任务 | 技术审查 |
|---|---|---|
| R01 | T01、T02、T10 | 数据模型、查询接口和员工界面均有交付与可观察验收 |
| R02 | T01、T03、T10 | 预约实体、并发控制接口与界面一致 |
| R03 | T04、T10 | 重复会议接口和冲突预览界面均有交付 |
| R04 | T05、T11 | 审批、超时释放与行政审批界面均有交付 |
| R05 | T06、T07、T12、T13 | 门禁契约、幂等同步、运维工作和恢复手册齐全 |
| R07 | T09、T12、T13 | 权限、审计、前台界面与演示数据均有覆盖 |

质量检查：

- 13 个任务均有交付物和可观察验收标准。
- 任务名称无重复，依赖引用均存在，服务端图校验为 valid。
- 每个任务均有需求引用，或具备明确过程依据。
- 未发现明显任务粒度过大、错误依赖或 must 需求遗漏。

正式签字区：

| 项目 | 值 |
|---|---|
| F 技术审查 | 建议通过，未发现阻断问题 |
| 非模块作者人工评审人 | 朱志伟 |
| 人工结论 | 通过 |
| 签字日期 | 2026.11.18 |

## 结论

- A04：真实模型、规格版本、attempt、usage 和结构化候选已记录，技术通过。
- A05：结构、覆盖、F 技术审查和非模块作者人工签字均已完成，正式通过。
- A11：需求变化与粒度变化均产生有意义差异，技术通过。
- Q06 真实时延：三次有效运行分别为 32.210、40.221 和 20.109 秒，均在边界内。

证据保存在本地：

```text
evidence/runs/20261118-stage4-real/
  real-acceptance-final2.json
  real-acceptance.json
  real-acceptance-final.json
```

`real-acceptance.json` 记录了初始成功运行和一次 NEEDS_INPUT；
`real-acceptance-final.json` 记录了 MODEL_BUDGET 有界失败；
`real-acceptance-final2.json` 是 A04/A05/A11 的最终成功证据。

公开 `PlanningRun` 成功响应未暴露 Provider request ID；当前 A04 依赖 run ID、REAL 模式、
attempt_count、usage、candidate 和 validation_report。若交付要求逐次供应商请求 ID，
需要扩展运行审计或补充 T04 内部证据。
