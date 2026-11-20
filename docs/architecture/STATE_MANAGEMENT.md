# 状态与恢复
当前 PlanningRun 和计划草稿状态唯一来源见 [领域模型](WORKFLOW_DATA_MODEL.md)。
PlanningRun 持久化后异步处理；重启将 QUEUED/RUNNING 标为 FAILED(PROCESS_INTERRUPTED)，不自动重发模型请求。SUCCEEDED/NEEDS_INPUT/FAILED 不可再变更，重试创建新请求。

## P2 执行状态
Run：CREATED → RUNNING → REVIEWING → COMPLETED；RUNNING/REVIEWING 可进入 WAITING_HUMAN，批准后恢复原阶段，拒绝/超时为 FAILED；所有活动状态可 CANCELED；异常退出为 FAILED(PROCESS_INTERRUPTED)。
TaskRun：PENDING → READY → RUNNING → COMPLETED，失败 FAILED，下游 BLOCKED，取消 CANCELED，在途中断 INTERRUPTED。重试新 Attempt，不覆盖旧结果。
终态不可覆盖。取消递增 generation 并停止派发；晚到响应不得将取消改为成功，费用仍可能产生。
修订新 PlanRevision 和新 Run，使用 parent_run_id 关联，不在运行中替换计划。

## 事件与恢复（P2）
状态/成果/事件同事务。SSE id=run_id:seq；GET快照含 snapshot_seq，再补发之后事件；重复去重、缺口重取快照、游标过期reset；心跳15秒。
跨重启 checkpoint 需与业务库对账，不能假设两库存储原子一致。采用 LangGraph 时恢复节点可能重入，因此审批节点不能包裹不可重复副作用，须用 operation_key。
当前 P0 无 SSE/运行恢复，后续实验通过才声明跨重启继续执行。
