# 模块详细设计
依赖方向为 api → application → domain/ports；adapters 实现 ports 并由入口注入，domain 不依赖 adapters；下表的接口为 Python 业务签名设计，HTTP 映射见 API。Validation/Orchestration 不导入 FastAPI、ORM 或 LLM SDK。

| 模块 | 职责 / 输入 → 输出 | 接口 / 内部数据 | 依赖 | 异常 / 测试 |
|---|---|---|---|---|
| Project | 工作区和归档 | create_project(name)→Project；UUID+名称 | Repository | 空名/重复请求；两项目隔离 |
| Design Spec | 规范化/版本/校验；content→SpecRevision | normalize/validate/save(expected_revision)；Requirement map | Validation、Repository | 必填/重复 ID/409；往返和版本测试 |
| PM Agent | 规格语义→TaskProposal | plan(spec,config)；Prompt、尝试账本 | Provider、Validation | 缺信息/非法 JSON；fixture+REAL评审 |
| Task Decomposition | 候选质量与覆盖 | assess(tasks,spec,policy)→Report；需求反向索引 | domain DTO | 漏需求/重复；变异案例 |
| Orchestration | 合法任务→DAG批次 | build_plan(...)；邻接表/入度表 | Validation | 环/非法阶段；菱形/多根/环性质测试 |
| Workflow Validation | 可复用硬规则与 warning | validate(...)->ValidationReport；rule registry | domain DTO | 字段路径；规则边界测试 |
| Visualization | 业务 DTO→React Flow 投影及草稿命令 | toCanvas/fromDraft；稳定 task map、layout | generated types、REST client | 陈旧响应/冲突；组件及 E2E |
| Persistence | 原子快照/CAS/历史/迁移 | UnitOfWork、SpecRepo、PlanRepo、PlanningRepo | SQLAlchemy/SQLite | 锁/事务回滚；临时库集成 |
| LLM Provider | 传输/错误/usage 标准化 | complete_json(...)->LLMResponse | HTTPS SDK | 401/429/timeout/empty；模拟传输 |
| PlanningPolicy | 解析可选规划限制 | resolve(optional_policy)→snapshot；缺省不限制业务阶段/角色 | Validation | 配置矛盾；参数约束测试 |

## P2 扩展契约
AgentRegistry.resolve(role_id,version)→{capabilities,input_schema,output_schema,tool_policy}；
AgentExecution.execute(task,input_manifest,context)→ArtifactRef[]；
ArtifactStore.put(attempt_id,content,schema)→{id,hash}；
Monitoring.append(event)→seq；
Verification.check(plan,artifact_refs)→{verdict,findings,evidence_refs}。
业务应用层连接接口，不允许 Agent 反向调用数据库修改 Run 状态。工具能力未定义前不开放任意 shell。
当前只保留接口设计，不新增空运行框架占位代码。
