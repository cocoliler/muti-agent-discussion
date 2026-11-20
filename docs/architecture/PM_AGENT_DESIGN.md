# PM Agent 详细设计
## 边界与接口
PM 理解已有规格并提出开发任务、交付、依赖、角色及假设，不执行工具、不写项目代码、不决定最终合法性。
async plan(spec: SpecRevision, config: PlanningConfig, provider: LLMProvider) -> TaskProposal。
Provider.complete_json(messages, model, max_tokens, timeout) -> {content,usage,provider_request_id,finish_reason}。
编排与持久化不依赖具体 SDK；DeepSeekAdapter 与 TestFixtureProvider 实现接口，TEST 请求只能选择明确 fixture_id，未知 fixture 拒绝。

## Prompt 合成
通用系统指令（版本 pm-v1）：
“你是软件开发规划 PM。输入是已经提供的设计规格，不重新替用户定义产品。只输出符合 TaskProposal Schema 的 JSON。逐需求安排可验收任务，source_requirement_ids 引用输入 ID。可选规划配置只能限制过程，不能替代产品功能。不要编造重要需求，关键缺失放 blocking_questions；非关键推断放 assumptions。dependencies 只引用候选任务。建议并行关系由独立编排器验证。不得输出执行结果或真实运行状态。”
用户消息包含 JSON 编码 spec.content、可选规划约束快照、粒度、规划限制和输出Schema；不注入固定产品任务或预设流程。
规格与补充文本视为数据，不能覆盖系统输出约束；不使用 eval 或从模型输出导入代码。
记录完整输入快照与 Prompt hash，而不记录隐藏推理；模型名、参数、provider request ID、响应、usage 和校验诊断可追踪。密钥不进入快照。

## 编排调用与失败
创建 PlanningRun 持久化 QUEUED 后进入本进程单规划队列；领取为 RUNNING。模型请求在数据库事务外。
先验证TaskProposal结构；合法结构中存在blocking_questions或critical assumption时直接NEEDS_INPUT，不对空任务集做正式DAG校验。没有阻断问题时再做任务/图校验，通过后SUCCEEDED；格式/网络/预算失败或有界修复耗尽则FAILED。
候选不自动保存正式 PlanRevision。保存时重新验证 hash 和假设确认；编辑后由 MANUAL 来源保留 planning_run_id。
规划调用硬上限三次（首次+修复/传输重试共享），单次超时 60 秒，从领取开始总截止 180 秒（每次请求超时取60秒与剩余截止时间的较小值）；429/暂时 5xx 可退避一次 1–3 秒，401 不重试。取消当前 HTTP 查询不取消后台请求。
输出上限默认 8192 token，输入按模型能力预检；总预留 40000 token/PlanningRun。请求前预留，usage 未返回按预留扣账并显示未知。模型/价格配置可修改，未配置价格不报精确费用。
超限、截断、空白响应、非法 JSON 分别输出 MODEL_BUDGET / MODEL_TRUNCATED / MODEL_EMPTY / MODEL_SCHEMA。网络失败不能伪造 fixture 结果。
进程退出导致未终结 PlanningRun 在重启时 FAILED(PROCESS_INTERRUPTED)，不自动再次产生费用；手动重试新 ID。

## 可配置与验证
参数只来自已验证的规划配置和请求快照，模型名从服务端 allowlist 选择；API Key 服务端环境变量读取。
P0 直接 Provider+Pydantic；LangChain 为需要多模型兼容时的备选，不同时装两套编排运行时。
[DeepSeek 官方 JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)说明格式模式与截断/空内容处理；业务 Schema 和语义质量由本系统额外校验。
实施阶段对用户实际提供的不同规格做 REAL 验证，并改变单项需求和粒度做变异检查；本轮不新增产品案例或预设模板。保存真实响应，不要求同一模型重复生成字节相同。
