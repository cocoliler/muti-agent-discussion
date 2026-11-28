# Host 草稿优先 · 阶段 2.3

## 实际运行流程

新输入使用现有的 `CLARIFYING/HOST_AGENDA` 等待点。默认派发专用的 `initial_outline` 逻辑任务，任务 ID 包含会话及输入版本。Host 同次返回工作草稿和后续讨论重点。Runner 用同一快照执行输出校验和有限重试，应用层用租约、状态版本、输入版本及快照阻止重复结果或旧结果写入。

- `CREATE_OUTLINE` 先过阶段 2.2 确定性门禁。工作草稿、质量结果、议程以及 `outline_committed` 里程碑在同一个 SQLite 事务提交。只有事务完成后才执行议程指定的专业讨论。
- `ASK_USER` 只接受阻断问题。服务端沿用问题 ID 并限制自动提问总数为 3。未解决问题保留到下次任务。提交过澄清后，Host 必须为每项已解决问题引用澄清后的新用户消息。超出额度后仍等待补充业务事实，工作台展示卡住的原因。
- 非阻断警告随草稿保存。若输出非法，Runner 每次执行最多尝试两次。两次失败后展示失败状态，可通过现有重试入口重新执行。
- 正式候选冻结、Reviewer 独立评审和用户签收保持各自入口。新版工作草稿仍使用工作草稿存储域。输入更新时，旧工作草稿按原 ID 留在历史里。

实现借鉴了用程序安排流程、把初稿生成与后续工作分开，以及把输出校验放在状态提交之前的做法。初稿任务调用 Host 一次。程序负责校验，失败时才进入受限修复循环。DeepSeek Provider 和原编排器负责实际执行。 [Anthropic 工作流模式](https://www.anthropic.com/research/building-effective-agents#building-blocks-workflows-and-agents) · [OpenAI Agents SDK 编排](https://openai.github.io/openai-agents-python/multi_agent/) · [输出 Guardrails](https://openai.github.io/openai-agents-python/guardrails/#output-guardrails)

## 配置与可观测性

- `DISCUSSION_INITIAL_DRAFT_MODE=draft_first` 为默认值。`discussion_first` 只排议程，先经过专业讨论再生成候选稿。`legacy_outline` 恢复旧版扁平初始框架的兼容行为。
- `DISCUSSION_INITIAL_CONSTRAINTS` 接受部署方提供的企业规则 JSON 数组，条目包含 `id`、`text`、`origin: "enterprise"` 和 `rule_ref`。用户提供的 `约束：...` 由程序提取，Host 补充的其他硬约束必须逐条引用用户原文。新版工作稿沿用默认模板和生成策略，真实 Provider 的输出预算取现有上限与策略预算中的较小值。
- 讨论详情和工作草稿详情同时返回现有卡片投影、结构化工作草稿和门禁结果。结构化数据记录工作稿标识、会话、输入和修订版本、模板与策略版本、来源、快照、创建时间及生命周期。
- `draft-metrics` 提供服务端发布里程碑、每次尝试的用量和校验诊断，以及初稿前澄清批次和实际提问数。发布日期差值用于计算服务端耗时。缺失的真实用量以 `null` 表示，模拟器的总量 0 为模拟标记。

### 本地受控模拟

用同一会议室预约输入，各模式独立 SQLite 重复三次。时间为从接收输入到首份成果的服务端落库中位数。两种模式分别以工作草稿和冻结候选稿为终点。

| 模式 | 首份成果 | Host 与专业调用合计 | 结构重试 | 前置澄清 | 中位数 |
| --- | --- | ---: | ---: | ---: | ---: |
| 草稿优先 | 工作草稿 | 1 | 0 | 0 | 67 ms |
| 讨论优先 | 冻结候选稿 | 8 | 0 | 0 | 578 ms |

没有发起真实 DeepSeek API 调用。MockTransport 验证预算确实写入请求，并验证了输入与输出 Token 的分项透传。真实模型的首稿质量、延迟与用量仍待实测。当前显式约束提取覆盖带标签的文本，其他约束需要 Host 引用原文。企业规则可注入可信约束，外部企业审批系统的连接不在本阶段范围。

## 代码位置与回归

- 专用任务输入、输出、结构校验与角色请求接入。 [generation.py](../../backend/initial_drafts/generation.py) · [contracts.py](../../backend/roles/contracts.py) · [provider.py](../../backend/roles/provider.py) · [simulation.py](../../backend/roles/simulation.py)
- 初稿提交、快照与询问预算状态，以及现有投影和计量接口。 [engine.py](../../discussion_orchestration/engine.py) · [models.py](../../discussion_orchestration/models.py) · [agent_execution.py](../../backend/discussions/agent_execution.py) · [application.py](../../backend/discussions/application.py) · [repository.py](../../backend/discussions/repository.py) · [schemas.py](../../backend/discussions/schemas.py)
- 工作台继续使用原草稿卡片和澄清区，增加警告、讨论消息链接和新澄清的原因提示。 [DiscussionCards.tsx](../../frontend/src/DiscussionCards.tsx) · [ClarificationForm.tsx](../../frontend/src/ClarificationForm.tsx) · [Workbench.tsx](../../frontend/src/Workbench.tsx) · [discussions.ts](../../frontend/src/discussions.ts)
- 新流程、旧版兼容路径、真实使用量适配测试均使用可复现的离线输入。 [新版流程测试](../../tests/test_host_working_draft.py) · [旧版初稿测试](../../tests/test_initial_outline.py) · [讨论编排测试](../../tests/test_discussion_agent_execution.py) · [Provider 测试](../../tests/test_role_provider.py)
