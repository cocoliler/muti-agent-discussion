# 最终 SE Workflow 扩展架构（P2）
## 规划图与运行图
PlanRevision 是冻结的业务任务 DAG；运行图是如何调度、调用工具、持久化、审批和验证这些任务的控制流程。
一个任务可有多次 Attempt、多份 Artifact；不能将一个 React Flow 节点等同于一次模型调用。一个计划可创建多次 Run。
角色注册可包含 PM、Architect、Developer、QA、Reviewer，也可配置其他角色；没有永久固定的五角色主链。

## 扩展顺序与候选方案
Phase 7 先以受限、可观察的 Agent 执行任务和传递成果；Phase 8 增加并行、监督、人工门控和验证；Phase 9 故障恢复、质量与性能。
候选 A：固定 LangGraph 主图 load→dispatch→collect→verify→review，动态任务作为数据；候选 B：普通 Python 状态机。
推荐先做 A/B 小实验，若 LangGraph 的持久等待及状态恢复收益超过依赖/对账成本，则选择 A。当前没有预先强制执行框架。
每次将计划编译为独立图会加重版本恢复与动态修改成本，不作为首选。

## 执行契约
Run={id,plan_revision_id,status,graph_version,generation,budget,last_seq,parent_run_id?}。
TaskRun={run_id,task_id,status,agent_version,active_attempt_id?}。
Attempt={id,task_run_id,number,input_manifest,started_at,ended_at,error?}。
Artifact={id,attempt_id,deliverable_id,schema_id,hash,content_ref}。
ExecutionBinding={task_id,agent_id,agent_version,input_bindings,output_schema_ids,tool_policy}，编译时补齐；不能直接执行只有建议角色的计划。
输入 manifest 明确源 Artifact ID/hash 和读取范围；同一计划里的 deliverable_id 只是一项预期成果。
Reviewer 返回 {verdict:pass/revise/fail,findings:[{task_id,requirement_id,evidence_ref,reason,suggestion}]}；确定性检查失败不可覆盖。

## 调度和人机协作
就绪任务的所有依赖须 COMPLETED 且输入可解析。按固定 task_id 顺序领取，初始并发上限2（待测参数）；事务写 Attempt 后调用 Provider，结果按 generation 和 attempt_id 去重入库。
失败下游 BLOCKED，独立分支可完成；失败、取消、重试都保留证据。预算在派发前预留，不因修订重置。
执行前确认精确计划 hash 和工具权限。修订须生成新 PlanRevision，并创建关联的新 Run；旧 Run 不原地换图，避免 v3 active_revision_id 的双重语义。
工具先支持只读材料及隔离成果写入；代码执行、构建和网络访问需定义工作区边界与人工授权，禁止把任意 shell 当默认能力。
人工作出决定检查 approval target_hash、期限和当前状态。输出审查、工具授权和执行批准是不同门控，不互相替代。

## 生命周期配置
PlanningPolicy 定义活动阶段及约束，AgentRegistry 定义角色能力，ExecutionBinding 决定实际角色映射；流程模板不包含固定业务任务。
跨次反馈通过新规格/新计划/关联运行表达。当前计划是 DAG，不通过增加回边实现返工循环。
