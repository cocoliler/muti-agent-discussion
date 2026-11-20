# 领域模型与工作流 Schema
## 术语和实体
本篇解释领域术语、状态和版本。精确字段、类型与可执行约束以 [Pydantic 模型](../../tech/flowstudio_contracts/)和[生成的 JSON Schema](../../tech/artifacts/schema/)为准，业务图约束见验证专篇。

| 实体 | 标识和主要字段 | 生命周期/关系 |
|---|---|---|
| Project | id(UUID), name, archived_at?, created_at | 本地产品工作区 |
| DesignSpec | id, project_id, latest_revision | 逻辑规格身份 |
| SpecRevision | id, spec_id, revision(int≥1), content:DesignSpecContent, hash, created_at | 不可变；DesignSpecVersion 同义名 |
| PlanningPolicy | schema_version, rules_version, allowed_stages?, allowed_roles?, allowed_categories?, allowed_stage_edges?, required_categories | 可选值对象；默认无额外限制，随请求保存快照 |
| PlanningRun | id, spec_revision_id, config_snapshot, mode:REAL/TEST, status, attempt_count, usage?, error?, proposal?, created_at | 单次规划请求及诊断 |
| WorkflowPlan | id, project_id, latest_revision | 计划身份 |
| PlanRevision | id, plan_id, revision, spec_revision_id, planning_run_id?, parent_revision_id?, policy_snapshot, source:AI/TEST/MANUAL, origin_mode:REAL/TEST/NONE, confirmed_assumption_ids, tasks, edges, execution_stages, parallel_batches, ordered_task_ids, validation_report, hash, created_at | WorkflowVersion 同义名；不可变已保存快照 |
| Task | 计划内稳定 task_id；详见任务专篇 | 不携带真实执行状态 |
| TaskDependency | revision_id, source_task_id, target_task_id | 由 tasks.dependencies 派生 |
| ValidationReport | valid:boolean, errors:Issue[], warnings:Issue[], coverage:{covered_ids,uncovered_ids}, rules_version, input_hash | 报告只适用于给定输入 |
| Layout | plan_revision_id, revision, positions, notes, viewport | 独立可更新，不改变计划 hash |
| Run / TaskRun / Artifact | 绑定 PlanRevision；实际执行和产物 | 仅 P2，不能与 PlanningRun 合并 |

所有全局 ID 为 UUID，task/requirement 内 ID 是受约束字符串；UTC RFC3339 时间；业务 JSON 按键排序、无多余空格、UTF-8 规范化后 SHA-256。
hash 包括规格引用、任务、规划约束快照、来源、origin_mode、confirmed_assumption_ids；排除 ID、创建时间、layout、报告生成时间和运行信息。
建议 JSON 导出同时带 spec_snapshot、policy_snapshot，便于离线解释；导入为新项目/计划时新建全局 ID，保留内部 task_id 和原始来源信息。

## 关系事实源
tasks[].dependencies 是输入唯一事实源：B.dependencies=["A"] 表示 A→B；响应 edges 为 {source:"A",target:"B"}。客户端不能提交派生 edges/order 作为权威，服务端重新计算。
execution_stages=[{stage_id,task_ids}] 来自任务 stage；parallel_batches 是拓扑层，可跨多个业务阶段。批次内 task_id 排序；ordered_task_ids 为逐层展平。
P0 不定义通用条件边、循环边和 handoff 边。注释、阶段容器、虚拟起止标记均非业务任务，不能计入覆盖率/拓扑。
输入引用使用直接依赖，一致性校验拒绝仅有祖先关系的隐式取值；需补充直接依赖再保存。

## 状态定义
PlanningRun：QUEUED → RUNNING → SUCCEEDED / NEEDS_INPUT / FAILED。
SUCCEEDED 代表候选通过结构与图校验，不代表用户接受假设或任务已执行。NEEDS_INPUT 保存问题；FAILED 保存原始诊断。重试新建 PlanningRun，旧记录不变。
UI 草稿状态：dirty / validating / valid / invalid；来源是否旧版是独立 outdated 标识。
PlanRevision 只保存有效且假设已确认的版本。无效草稿可保留在本地但不能成为正式 PlanRevision。
后续 Run 状态在运行专篇定义，当前画布绝不给未执行节点标 COMPLETED。

## 版本与并发
保存新规格/计划用 expected_revision。事务 compare-and-swap latest_revision，失败返回 409，携当前版本；不自动覆盖。
重新生成的 PlanningRun 不自动移动计划 head，用户确认保存才新建版本。网络重复命令由 Idempotency-Key 去重。
修改布局用独立 layout revision；历史业务只读，编辑历史先复制成新候选；以当前 head 为 expected_revision、历史为 parent_revision_id。
规格变更不自动把已有任务改名；旧计划仍可读，重新生成并人工比较。
旧前端 id/title/depends_on 映射到 task_id/name/dependencies；缺角色、需求和交付字段的旧草稿进入待补全候选，禁止直接判定为有效计划。

## 保存与来源不变量
PlanRevision同时保存confirmed_assumption_ids和origin_mode（REAL/TEST/NONE）；MANUAL只表示经过人工编辑，不能抹掉TEST起源。保存请求携planning_run_id时核对项目、规格和policy一致。
阶段展示顺序按该阶段最早拓扑层、再stage_id稳定排序，不把阶段名当算法枚举。所有输出排序确定；无额外policy时允许PM提出不同阶段/角色。
