# Agent 通信（P2）
当前 PM→编排器传递 TaskProposal JSON，不需要消息总线。
未来消息由单体应用服务存储：{message_id,run_id,task_id,attempt_id,sender,receiver,type,payload,timestamp,correlation_id,causation_id}。
类型 TASK_DELEGATION 指向输入 manifest；ARTIFACT_HANDOFF 指向产物 ID/hash；TASK_RESULT 指向结果；REVIEW_FEEDBACK 指向证据。
dependencies 是计划关系，InputRef 是预期成果引用，Message 是实际发生的交接记录；画布连线不能冒充已发生消息。
消息按 ID 去重、日志脱敏，只传必要字段；接收 Agent 不能直接访问其他项目成果。详情和执行绑定见 [扩展](SE_WORKFLOW_EXTENSION.md)。
不引入 Kafka、RabbitMQ 或第二套消息系统。
