# 技术选型与决策依据

> 合并自 `docs/architecture/TECH_STACK.md`、`docs/architecture/ARCHITECTURE_DECISIONS.md`、
> `docs/research/REFERENCE_PROJECTS.md`。日期 2026-11-15。
>
> 三类结论严格区分：**已实测的版本**、**团队决策（未实测）**、**明确不采用**。
> 不把推荐写成兼容认证，不用"最佳实践"代替实测。

## 目录

1. [选型总表](#1-选型总表)
2. [架构决策记录](#2-架构决策记录)
3. [实测锁定的版本](#3-实测锁定的版本)
4. [未验证清单](#4-未验证清单)
5. [参考项目研究](#5-参考项目研究)
6. [调研如何落地到契约](#6-调研如何落地到契约)
7. [证据边界与许可](#7-证据边界与许可)

---

## 1 选型总表

所有版本在实施时锁定兼容集；当前 `frontend/package-lock.json` 已有 React 19 / React Flow 12 / Vite 7
系列依赖，不为文档强制升级。

| 领域 | 比较 | 结论与触发条件 |
|---|---|---|
| 前端 | 现有 React/TS/Vite vs 重建 Next.js | 保留现有，不需要 SSR |
| 画布 | React Flow vs 自绘 Canvas | 保留前者交互基础；业务图独立 |
| UI | 现有 CSS vs Tailwind+shadcn/ui | P0 统一 CSS tokens 复用现有；新增复杂可访问组件再评估局部引入，避免全量重写 |
| 布局 | 现有拓扑分层 vs Dagre/ELK | P0 分层；跨组连线/大图测量不达标时做候选实验 |
| 后端 | FastAPI vs 大型服务框架 | FastAPI+Pydantic，契约清晰、本地单体 |
| LLM | 直接 Provider 适配 vs LangChain | P0 直接适配，结构化 Schema 和预算自主控制；多供应商复杂度上升才考虑 LangChain |
| 编排 | Python 纯函数 vs LangGraph | P0 纯函数；P2 对持久等待/调度做框架实验 |
| 存储 | SQLite/SQLAlchemy vs PostgreSQL | 本地单用户选择 SQLite、迁移用 Alembic，不引入外部 DB |
| 实时 | REST 轮询 vs SSE vs WebSocket | P0 轮询；P2 单向运行事件用 SSE；无实时多人协作，不选 WebSocket |
| 工程 | 单体 vs 微服务/队列 | 单进程、持久 PlanningRun + 有界队列；中断显式失败，不引入 Redis/Docker |

FastAPI、SQLAlchemy、SQLite/Alembic、项目/规格/规划/计划 API、P0 HTTP 验收与真实模型调用均已落地；跨平台兼容仍以各自环境实测为准。

---

## 2 架构决策记录

状态：P0 决策已实施并经统一验收验证；P2 框架实验仍待后续验证。更新于 2026-11-18。

| ADR | 原问题 / 决定 | 影响与代价 |
|---|---|---|
| 001 | v3 混合规划和执行；明确作业 2 独立闭环，最终生命周期保留 | 降低首期依赖，不删最终目标 |
| 002 | LLM 不可作为合法性裁判；PM 候选 + 确定性图算法 | 需要明确 Schema 及错误修复 |
| 003 | SpecRevision/PlanRevision 已使用；保留并定义同义实体 | 不额外创建 DesignSpecVersion/WorkflowVersion 表 |
| 004 | 原计划包含 assigned_agent/output_schema；改建议角色与预期交付 | 执行前须编译 ExecutionBinding |
| 005 | v3 固定 LangGraph 为首期核心；改 P2 候选，P0 纯函数 | 当前无需持久执行框架；后续需实验 |
| 006 | 画布 toObject 不足业务保存；业务与 Layout 分离 | 多一层 DTO 适配，换画布库不影响计划 |
| 007 | 历史可重现；不可变版本 + CAS + 幂等键 | 保存请求稍复杂，避免误覆盖 |
| 008 | 不让返工在冻结 DAG 中加回边；新 PlanRevision + 新 Run | 不复用 active_revision_id 变图模式 |
| 009 | 任务/依赖关系化，规格及短详情数组 JSON | 避免巨型 JSON 或过度规范化 |
| 010 | UI 已有资产保留，CSS tokens 先行 | 不为技术栈清单迁移全部组件 |
| 011 | 保留现有 docs 目录，专篇内容唯一维护 | README 映射用户要求的成果类别 |
| 012 | 单机 REST 轮询、重启显式中断 | 不承诺服务重启后自动继续付费调用 |

参考依据集中在第 5 节；未验证事项登记在第 4 节，不用"最佳实践"代替实测。

---

## 3 实测锁定的版本

只记录真正运行过的组合，未安装的依赖不声称兼容。

| 组件 | 实测版本 | 证据 |
|---|---|---|
| Python | 3.14.2 | 契约测试与导出脚本实际运行 |
| pydantic | 2.13.5（`tech/requirements.txt` 锁定） | 严格模型、JSON Schema 生成、OpenAPI/TS 导出 |
| jsonschema | 4.26.0（开发版 `requirements-dev.txt`，本交付包不含） | Draft 2020-12 元校验与样例回放 |
| Node.js / npm | 22.15.0 / 10.9.2 | 生成的 TS 通过 `node --experimental-strip-types --check` |
| React / @xyflow/react / Vite / TypeScript | 19.2 / 12.10 / 7.3 / 5.9（见 `frontend/package.json`、`package-lock.json`） | 既有前端原型依赖，本轮未升级 |
| FastAPI | 0.163.0 | 本机探针：`GET /health` 返回 200 `{"status":"ok"}`，`/openapi.json` 输出 3.1.0 |
| uvicorn | 0.54.0 | 同上，127.0.0.1 单进程启动后正常退出，无启动错误 |
| SQLAlchemy | 2.0.49 | `sqlite://` 建表、插入、查询往返成功 |

探针是一次性临时脚本，不入仓库；它只证明上述三件能在 Windows/Python 3.14.2 上启动与读写，
不代表 T02 的 API 已实现。

---

## 4 未验证清单

以下项目**没有**运行过，不得写成已验证兼容：

- **Alembic 迁移**：未执行任何升级/降级脚本。
- **DeepSeek SDK 调用**：只有接口设计，无真实模型调用记录；质量、延时、token 成本均未实测。
- **uvicorn 多进程与 Windows 服务化**：只验证了单进程 127.0.0.1 启动。
- **SQLite 落库**：只做了内存库往返，没有真实文件库、WAL、并发 CAS 的测试。
- **自动布局（Dagre/ELK）**：未选定，P0 用现有拓扑分层；需要真实图规模实验。
- **`PlanningRun.usage`**：只定义字段，不预填结果。
- **执行期沙箱、工具白名单与工件校验**：只有边界设计，无实现。

上表以外的组合，一律不写成"已验证兼容"。

---

## 5 参考项目研究

调研日期 2026-11-15。实际读取下列官方页面与部分源码；**未运行这些项目，未锁定仓库 commit**。
结论限于列明文件，应用建议是 FlowStudio 设计判断，不是性能实测。源码以链接供团队复核，
不复制其实现或产品资源。

| 项目 / 研究模块与证据 | 已确认机制 | 本项目应用 / 不采用项 | 成本与风险 |
|---|---|---|---|
| Dify [workflow/index.tsx](https://github.com/langgenius/dify/blob/main/web/app/components/workflow/index.tsx) | nodeTypes/edgeTypes 注册、自定义注释、选择模式、draft 同步 hook、只读模式与变量类型匹配入口 | 保留主画布+按需详情；独立业务/布局；不搬运多人协作、数据集和工具市场 | 中；只读入口文件，未验证所有 hook 的内部事务 |
| n8n [execution order](https://docs.n8n.io/flow-logic/execution-order/) 与 [executions](https://docs.n8n.io/workflows/executions/) | 运行记录独立；官方说明部分执行规则与画布位置相关，多分支不等于并发 | 借鉴连线和历史表达；FlowStudio 明确依赖决定拓扑、布局无业务效力 | 低至中；不能照搬自动化节点语义到 SE 任务 |
| Flowise [Interface.ts](https://github.com/FlowiseAI/Flowise/blob/main/packages/components/src/Interface.ts)、[Agentflow V2](https://docs.flowiseai.com/using-flowise/agentflowv2) | INodeProperties/INodeParams/INodeData 分离元数据、配置和实例；输入输出及动态选项接口；流程状态与节点交接 | 建立 Task renderer registry、受控详情配置和显式输入引用；不引入任意 code 节点或实例序列化 | 中；部分接口 any 不应复制到严格领域 DTO |
| LangGraph [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | checkpoint/thread_id 与人工恢复；恢复可能重入节点 | P2 候选持久等待；副作用去重与业务库对账；不替代当前纯 DAG 算法 | 高于纯函数；恢复复杂度需 Windows 实验 |
| React Flow [Custom Nodes](https://reactflow.dev/learn/customization/custom-nodes)、[Save and Restore](https://reactflow.dev/examples/interaction/save-and-restore) | 自定义组件与 handles、nodeTypes 注册；nodes/edges/viewport 可恢复 | 复用现有 @xyflow/react；坐标/注释独立，业务保存经过后端校验 | 低；撤销、布局和业务校验不能假定库自动实现 |
| MetaGPT [product_manager.py](https://github.com/FoundationAgents/MetaGPT/blob/main/metagpt/roles/product_manager.py) | ProductManager 角色组织 PrepareDocuments/WritePRD 等活动，固定 SOP 可按序处理 | 借鉴角色/活动/中间产物；本项目输入已有 Spec，PM 主要输出任务而非重新写 PRD | 中；职责名字相同不代表输入输出相同 |
| OpenHands [README 架构与仓库边界](https://github.com/OpenHands/OpenHands/blob/main/README.md) | Agent Canvas 与 Agent Server/SDK、工具/会话/事件分工 | 参考 UI 与执行边界、产物和受控工作区；不嵌入整套执行部署 | 高；本轮只验证 README 边界，未审计执行沙箱源码 |
| DeepSeek [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) | JSON 模式、提示示例、token 上限、空白/截断处理 | Provider 适配+严格 Schema+有界修复，不把合法 JSON 当合法计划 | 中；质量、延时、usage 与模型能力仍需真实测量 |

---

## 6 调研如何落地到契约

上表结论逐条对应到可检查的决策。只覆盖本次已实现的契约层；PM、编排、前端的具体算法仍由 C/D/B 决定。

| 来源机制 | 本项目决策 | 落地位置 | 可检查证据 |
|---|---|---|---|
| React Flow `nodes/edges/viewport` 可恢复 | 布局自成一体：位置、注释、视口独立版本，不进入业务字段 | `Layout` / `Viewport` / `CanvasNote` | `schema/layout.schema.json`、`examples/layout.json` |
| n8n 运行记录独立、画布位置不影响执行顺序 | `PlanningRun`（规划）与 `PlanRevision`（保存的计划）分离，后续执行 Run 再独立；坐标不产生依赖 | `planning.py`、`workflow.py` | `planning-run.schema.json`、契约测试中的 origin/source 用例 |
| Dify 主画布 + 按需详情、只读模式 | 任务只保留业务字段，展示状态不进 DTO；列表/详情由前端派生 | `Task` | `schema/task-proposal.schema.json` |
| Flowise 元数据/配置/实例分离 | 契约只描述数据形状，不携带任何 UI 实例或序列化句柄 | `StrictModel`（`extra="forbid"`） | 未知字段拒绝用例 |
| MetaGPT 角色活动与中间产物 | 任务显式声明 `assigned_role`、`category`、`inputs`，输入引用必须能被直连依赖解释 | `Task`、`InputRef` | `examples/task-proposal.json` |
| DeepSeek JSON 模式与截断/空白问题 | 不把"合法 JSON"当"合法计划"：严格 Schema + 有界修复 + 明确失败码 | `RunFailureCode`、`PlanningRun.error`、`attempt_count ≤ 3` | `examples/planning-run-failed.json` |
| LangGraph 中断恢复、OpenHands 执行边界 | 属 P2，不进入首期契约；但先固定"规划 ≠ 执行"的命名边界 | 实体表 Run/PlanningRun 行 | 见 `system-architecture.md` 第 4 节领域模型 |

---

## 7 证据边界与许可

**未验证范围**

- Flowise 最初尝试 `packages/components/nodes.ts` 返回 404，后通过目录定位到 `src/Interface.ts`；不得引用不存在文件。
- React Flow 自动布局概览两次超时；不据此宣称某布局器已验证。P0 保留本地已有拓扑分层，Dagre/ELK 作为后续候选实验。
- 未检查 n8n 编辑器源码、Flowise 实际 React Flow UI 注册实现、Dify 各节点执行和变量内部实现。
  当前 UI 取舍以入口源码、官方说明和用户提供截图为依据。
- 未锁定参考 commit；实施若复用接口或依赖必须固定版本再复核。

**许可与采用边界**

已查 [Dify LICENSE](https://github.com/langgenius/dify/blob/main/LICENSE) 与
[n8n LICENSE](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)，均含使用限制，不能概括为无条件宽松许可。
其他项目本轮未逐包核验 LICENSE/SPDX，不作可直接复制结论。这里只参考机制、独立编写设计；
如后续复制源码/图标/Pro 示例，必须逐项核验对应版本许可与署名要求。
