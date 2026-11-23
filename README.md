# 多 Agent 需求讨论工作台

这是 FlowStudio 的上游需求工作台。用户提交项目想法后，多个专业 Agent 在同一个项目群聊中讨论范围、约束和风险，记录员持续更新共识稿。独立评审检查需求依据与验收条件，用户签收通过评审的版本，生成可直接导入 FlowStudio 的结构化设计规格。

## 从想法到设计规格

1. 主持人整理背景、目标、用户和约束，请用户确认摘要或补充关键事实。
2. 产品、研究、架构、设计和商业分析角色并行发言。质疑者引用具体观点提出问题，相关角色定向回应。
3. 记录员将共识、分歧和风险整理为带版本的候选规格。每条需求可追溯到对应发言。
4. 独立评审给出通过、退回修订或需要补充的结论。修订保留需求 ID，并将新版本重新送审。
5. 用户签收通过评审的当前版本，导出 UTF-8 JSON 供 FlowStudio 导入。

## FlowStudio 交接

正式输出遵循 FlowStudio v3.1 的 `DesignSpecContent` 契约，`schema_version` 为 `"1.0"`。功能需求、非功能需求、优先级与验收条件使用现有字段。讨论消息、来源映射、风险和评审历史保存在工作台内，通过稳定需求 ID 关联。

首版以 JSON 导出和 FlowStudio 现有导入入口验收。后续可通过规格版本 API 创建正式 `SpecRevision`，交给 FlowStudio 的 PM Agent 继续任务规划和编排。 [规格 Schema](tech/artifacts/schema/design-spec.schema.json) · [交接设计](FLOWSTUDIO_DESIGN_INPUT.md#62-本地导入与-api-交接)

## 运行本地原型

需要 Python 3.11+ 和 Node.js 22.12+。在仓库根目录打开 PowerShell，执行以下命令。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开 [本地工作台](http://127.0.0.1:8000/)，点击「启动会议室演示」，随后按底部按钮逐阶段推进。评审退回后，查看草稿 v2 与 v1 的验收条件差异，确认签收并下载 JSON。会话保存在 `backend/.data/discussions.sqlite3`，同一浏览器刷新可继续演示。

原型采用 React、FastAPI 和 SQLite，内置会议室预约的固定模拟剧本，无需模型密钥。当前支持会话恢复、消息引用、草稿版本、评审退回和签收导出。浏览器演示使用 `/api/demo`；下述角色服务提供独立调用入口，供自由讨论流程接入。

| 阶段 | 验收内容 |
| --- | --- |
| 模拟讨论 | 通过会议室预约案例验证角色发言、引用质疑、独立评审退回、定向修订和刷新恢复 |
| 真实模型 | 验证角色输出校验、人工澄清、调用预算、失败恢复和成本记录 |
| 规格交接 | 签收当前版本，验证 JSON 导入，再验证 API 幂等写入和版本冲突处理 |

## 成员 2 · 运行与接入讨论角色

安装上面的后端依赖后，可直接运行校园器材借用样例。主持人读取用户原话，返回本轮议题、参与角色和消息引用。第二条命令运行角色回归测试。

```powershell
.\.venv\Scripts\python.exe -m backend.roles backend/fixtures/role-host-input.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

真实模式从服务端环境变量 `DEEPSEEK_API_KEY` 和 `DISCUSSION_MODEL` 读取凭据与模型名。将两个变量配置在当前终端后，执行以下命令。返回的 JSON 包含模式、模型名、任务快照摘要、输出和逐次调用记录。退出码 0 表示成功，1 表示角色执行失败，2 表示输入或配置有误。

```powershell
.\.venv\Scripts\python.exe -m backend.roles backend/fixtures/role-host-input.json --mode real --timeout 45 --call-limit 2
```

编排端为每个会话创建一个 `RoleRunner`，在同一个事件循环中复用它。主持人、五个专业角色、质疑者、记录员和独立评审共用以下接口。会话预算按实际尝试扣减，命令行每次启动是独立运行。

```python
from backend.roles.contracts import RoleRequest
from backend.roles.runner import RoleRunner
from backend.roles.simulation import SimulationProvider

async def run_session_tasks(session_id, snapshots):
    runner = RoleRunner(session_id, SimulationProvider(), mode="simulation", call_limit=32)
    results = []
    for snapshot in snapshots:
        result = await runner.run(RoleRequest.model_validate(snapshot))
        results.append(result)
        if result.error:
            break
    return results
```

| 接入方 | 调用约定 |
| --- | --- |
| 成员 1 · 编排 | 分配任务 ID、输入版本、可点名角色和本轮可见消息；送审时提供冻结稿及其依据。根据 `result.output` 决定是否推进。相同任务与快照重复派发会返回已有结果；同一 ID 换用新快照返回 `task_snapshot_conflict`。 |
| 成员 3 · 存储 | 保存任务、快照引用、结果和 `attempts`。每次尝试带序号、耗时、错误类别、诊断及服务端报告的 token 用量。缺失用量记为 `null`。运行器的去重和调用计数仅在当前进程内有效，跨重启的预算与历史由持久化层恢复。 |
| 成员 5 · 规格交接 | 记录员输出 `content`、按需求 ID 索引的 `source_map` 和需求变更说明；独立评审返回冻结稿 ID、结论与问题处理记录。签收时由上层校验当前版本和用户确认。 |

限流、超时和临时服务故障最多重试一次，每次都受剩余预算约束。格式异常返回 `invalid_output` 和诊断。额度耗尽返回 `call_limit`。编排端得到用户授权后可用 `runner.extend_budget(n)` 追加额度，再次提交同一快照以继续剩余尝试。模拟模式使用确定性示例规则，用量为零。真实服务的用量按每次响应记录。[角色输入输出契约](backend/roles/contracts.py) · [样例输入](backend/fixtures/role-host-input.json)

## 六人开发分工

| 负责人 | 开发范围与交付物 | 验收标准 |
| --- | --- | --- |
| 成员 1 · 讨论编排 | 实现会话状态机、议题调度、首轮并行、质疑与点名回应、人工澄清和定向修订。负责校验阶段迁移和轮次上限 | 必需发言和质疑齐备后才可成稿。评审退回只调度相关角色，旧输入版本的结果无法推进当前会话 |
| 成员 2 · Agent 角色与模型 | 实现主持人、专业角色、质疑者、记录员及评审的角色指令、上下文和结构化输出契约。提供模拟与真实模型适配、超时重试和用量采集 | 同一角色接口可切换模拟与真实模型。格式错误和超时可诊断，调用上限生效，用量可追踪 |
| 成员 3 · 后端 API 与持久化 | 实现 FastAPI 讨论接口和 SQLite 持久化，保存会话、消息、调用尝试、草稿与评审历史。负责消息序号、增量分页、事务和版本冲突处理 | 刷新或重启后可续读消息及查看历史。同一写入重试可幂等处理，跨会话引用会被拒绝 |
| 成员 4 · 前端讨论工作台 | 实现 React 群聊、消息引用跳转、角色进度、人工决策卡、共识稿侧栏和评审差异视图，接入增量轮询 | 用户能定位需求来源、补充信息、查看 v1/v2 差异，并在当前版本通过评审后签收 |
| 成员 5 · 规格校验与 FlowStudio 交接 | 实现草稿到 `DesignSpecContent` v1.0 的转换，负责需求 ID、来源覆盖、验收条件和签收校验。交付 JSON 导出及后续规格版本 API 适配 | 未通过当前版本评审或仍有阻断项时无法签收。签收后的 JSON 可用 FlowStudio 现有入口导入 |
| 成员 6 · 集成测试与本地演示 | 实现可重复的会议室预约演示数据和端到端测试，覆盖定向修订、人工补充、刷新恢复、超时与额度耗尽。提供 Windows 本地启动和演示说明 | 从项目想法到评审退回、修订通过、签收与导入可完整复现，关键失败路径有回归用例 |

联调先统一角色输出、会话状态、消息接口及规格契约，再用模拟 Agent 跑通会议室预约案例。随后接入真实模型，并用 FlowStudio 实际导入结果验证交付。

## 设计文档

- [多 Agent 讨论与设计规格生成](FLOWSTUDIO_DESIGN_INPUT.md)
- [FlowStudio v3.1 基线](BASELINE.md)
- [设计规格模型](architecture/DESIGN_SPEC_MODEL.md)
- [FlowStudio REST API](architecture/API_SPECIFICATION.md)
- [讨论编排内核](discussion_orchestration/README.md)
- [规格校验与 FlowStudio 交接](spec_handoff/README.md)
