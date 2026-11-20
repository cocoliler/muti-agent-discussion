# 确定性编排器
## 定位
当前编排器是无模型、无数据库副作用的纯函数：
build_plan(tasks, spec, policy) -> {edges,execution_stages,parallel_batches,ordered_task_ids,validation_report}。
只有通过校验的结果可形成 PlanRevision。未来调度器消费冻结计划，见扩展架构；不在此混入 LangGraph 执行。

## 图语义和阶段
每个 Task 为顶点，依赖 A→B 表示 B 必须等待 A。多个父节点为 AND 汇合。无条件、循环或 OR 分支。
阶段是开发活动分类，拓扑层是依赖计算结果；阶段标签不隐式产生依赖。若提供跨阶段关系约束则必须满足，用户需改 stage 或依赖，不能通过布局修复。
支持多个根/叶节点和独立组件；不要求全部任务从某个业务起点可达。孤立任务有规格/过程依据则合法，缺依据给警告；选定交付目标时才检查其祖先覆盖，不随意删除未关联任务。

## 算法伪代码
```text
build(tasks, spec, policy):
  validate_sizes_and_schema()
  id_map = unique_map(tasks.task_id)     # 重复立即返回带字段路径错误
  indegree = {id: 0}; outgoing = {id: []}
  for task in tasks:
    validate_role_stage_category_and_requirement_refs(task)
    seen = set()
    for dep in task.dependencies:
      reject missing / self / duplicate dependency
      validate_allowed_stage_transition(dep, task)
      outgoing[dep].append(task.id)
      indegree[task.id] += 1
    validate_inputs_and_deliverables(task, id_map) # task 输入须直接依赖
  if errors: return invalid_report
  frontier = sorted(all ids whose indegree == 0)
  batches = []; count = 0
  while frontier:
    batch = frontier                     # 本轮集合冻结
    batches.append(batch); count += len(batch)
    next_frontier = []
    for u in batch:
      for v in outgoing[u]:
        indegree[v] -= 1
        if indegree[v] == 0: next_frontier.append(v)
    frontier = sorted(next_frontier)
  if count != len(tasks):
    cycle = iterative_three_color_DFS(unprocessed_subgraph)
    return CYCLE with actual cycle task_ids and edges
  check_requirement_coverage_and_required_categories()
  check_orphans_and_duplicate_titles_as_warnings()
  return edges, group_by_stage(tasks), batches, flatten(batches), report
```

Kahn 未处理集合包含环的下游，不得全部标为“环上的节点”；三色 DFS 只报告回边及栈内实际环，UI 定位那组边。
一个菱形：A→B、A→C、B→D、C→D，结果 [[A],[B,C],[D]]；不能把逐个入队序列误当并行批次。
空任务（无阻断问题）非法；仅一个有据任务合法。

## 复杂度与重建
建图/Kahn/DFS O(V+E)，稳定排序使总上界 O(V log V + E)，空间 O(V+E)；引用及覆盖额外 O(R+I)，R 为需求数、I 为引用数。精确重复文本散列 O(文本总长)，不做全对全 LLM 比较。
P0 上限 100 tasks/1000 edges；编辑后全量重算，避免增量缓存失效复杂度。前端立即检查连线错误，服务端最终全量检查。
修改 task/依赖标 dirty，旧批次显示“待重新编排”。请求携草稿 hash，响应 hash 不匹配当前草稿则丢弃显示，避免慢请求覆盖新图。
模型提出的顺序不是事实源；无耗时依据不显示关键路径时间。批次是静态依赖并行机会，不是资源调度承诺。

## 实现映射（T05 已交付，2026-11-15）

代码在 `backend/flowstudio_orchestration/`，只依赖 T01 契约，不导入 FastAPI、ORM 或 LLM SDK。

- 入口：`build_plan(tasks, spec, policy) -> PlanDerivation`；`validate_workflow(...) -> ValidationReport`
  复用同一份实现，`/workflow-validations` 不需要第二套规则。
- 规则：`rules.py` 按结构 → 图 → 覆盖 → 告警注册纯函数，规则 id 与 `rules_version` 一起进入报告。
- 哈希：`input_hash` 取规格内容、任务、规划约束快照与规则版本的规范化 SHA-256，不含数据库 ID、时间与布局。
  它与 `artifacts/examples/plan-derivation.json` 的样例哈希不同，因为样例只散列了规格。
- 规模：空任务由请求契约（`min_length=1`）拒绝；超过 100 任务 / 1000 依赖抛 `OrchestrationLimitError`
  （带 limit/actual），由 API 映射 422，报告里不伪造错误码。
- 无效输入：返回空 edges/stages/batches/order 与 `valid=false`，不返回部分批次，避免前端误用。
- 验收：`cd backend && python -m unittest discover -s tests -v`（53 项）；官方样例的边/阶段/批次/顺序与
  `examples/plan-derivation.json` 一致；100 任务/1000 边实测 p95≈3.9ms（Linux，Python 3.12，非 Windows 基准）。
