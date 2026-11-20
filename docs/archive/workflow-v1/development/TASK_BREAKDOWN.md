# 可逐项交给 Codex 的开发任务

当前仅完成设计，以下均是**下一步获得实现指令后**的任务；不能因本清单存在就自动开始实现。每项以一个可独立检查的成果为边界，预计0.5–3个开发人日，超出则继续拆分。实际完成状态、PR和测试证据应另在任务跟踪记录中填写，本轮不提前打勾。

## 任务调用方式

给 Codex 的每次输入由“任务ID + 输入文档 + 范围 + 验收”组成，例如：

> 实现 T5.2。先读 docs/BASELINE.md、docs/architecture/WORKFLOW_ENGINE_DESIGN.md 和已有源码。只修改 packages/workflow-core 的分支与 Join 推进逻辑及必要测试；不得增加新节点、改变枚举或引入服务。按任务夹具证明未选分支不阻塞、两个激活并行分支均被等待；报告改动与实际测试结果。接口冲突先指出具体字段并提出最小修订，不能静默另造语义。

每项输出必须包含代码/文档差异、可执行验证方式、实际结果和剩余限制。示例和夹具允许使用测试专用 executor；不得把 mock 成功路径接入产品运行按钮。任务表的“修改边界”为默认允许范围，跨越时先说明必要性，并同步对应契约/文档。

## Phase 0：研究与需求

|ID|任务与输入|修改边界及交付|依赖|独立验收/检查|
|---|---|---|---|---|
|T0.1|核查五项目关键机制和许可证|research来源与事实/建议标记|用户需求|来源具体、访问限制明确、无“只看首页”结论|
|T0.2|冻结产品范围与设计基线|product五文档、BASELINE|T0.1|FR/NFR可验收，P0/P1/Out of Scope一致|
|T0.3|详设自审与任务追踪|DESIGN_REVIEW及本清单|T0.2及架构/UI详设|关键缺陷落实修订；任务能追踪全部FR|

T0.1–T0.3 是本轮文档工作；它们的完成不表示以下实现已完成。

## Phase 1：契约、环境、数据库基础

|ID|任务与输入|修改边界及交付|依赖|独立验收/测试|
|---|---|---|---|---|
|T1.1|定义格式/版本/状态/错误契约；读MODULE/API/ENGINE|contracts + schema fixture；新增最小workspaces配置|T0.3|所有示例通过schema；未知版本/额外危险字段拒绝；TS类型从单一schema导出|
|T1.2|SQLite表与迁移；读DATABASE|server/src/persistence；迁移、repository ports、db:init/db:check|T1.1|空库/旧fixture升级，事务回滚、FK/unique/CHECK；不改已有迁移|
|T1.3|Windows安装与生命周期骨架；读LOCAL_SETUP|根scripts、server入口、安装证据|T1.1,T1.2|Win10/11无C++工具链安装，内嵌SQLite≥3.51.3；同目录双实例拒绝；停止无孤儿进程|

T1.3 未通过前不把技术栈宣布为已验证。若失败，修订技术决策并重估后续工作，不通过加云数据库规避。

## Phase 2：UI规范与可查看原型

|ID|任务与输入|修改边界及交付|依赖|独立验收/测试|
|---|---|---|---|---|
|T2.1|设计token与通用组件；读DESIGN_SYSTEM|web样式、Button/Input/Panel/Status/Toast/Dialog|T1.1|焦点、禁用、错误、loading状态齐全；对比度与缩放检查|
|T2.2|八类页面路由壳与fixture原型；读IA/UI_UX|web路由/shell/页面；明确演示数据标记|T2.1|两种桌面尺寸可导航；空/失败态可查看；运行不伪称完成|

## Phase 3：可视化编辑器

|ID|任务与输入|修改边界及交付|依赖|独立验收/测试|
|---|---|---|---|---|
|T3.1|ReactFlow适配与基本编辑；读EDITOR/MODULE|web/features/editor、node-catalog描述导出|T2.1,T1.1|拖放/连线/删除/缩放/多选；保存序列化不含执行状态|
|T3.2|节点配置与变量引用表单|属性面板、受控表单、structured binding picker|T3.1|类型显示、JSON Pointer、可选fallback；必填错误定位到字段|
|T3.3|编辑命令历史与子图复制|editor command store及测试|T3.1,T3.2|撤销重做不丢配置；批量操作单历史项；新ID及内部引用同步|
|T3.4|视觉分组与两级Dagre布局|editor layout/group adapter|T3.3|非嵌套组、跨组边保留、自动布局可撤销；组移动不改执行依赖|
|T3.5|纯校验器与画布诊断|workflow-core validator、diagnostic list|T1.1,T3.2|环/端口/不可达/类型/引用/分支/任务映射夹具；点击定位正确节点|

## Phase 4：工作流管理与一致保存

|ID|任务与输入|修改边界及交付|依赖|独立验收/测试|
|---|---|---|---|---|
|T4.1|项目、工作流、规格和任务CRUD|server应用服务/routes、web列表与规格任务页|T1.2,T2.2|创建搜索编辑软删；任务映射一对多；活动运行删除冲突可解释|
|T4.2|统一draft revision与自动保存|draft repository/API、web保存协调器|T4.1,T3.3|连续编辑不乱序保存，409保留本地内容；规格/tasks/图/layout一致提交|
|T4.3|发布版本与历史只读定义|version service/API、版本选择UI|T4.2,T3.5|服务器校验后冻结同一revision；后续草稿编辑不改变版本hash|

## Phase 5：真实执行引擎

|ID|任务与输入|修改边界及交付|依赖|独立验收/测试|
|---|---|---|---|---|
|T5.1|快照运行创建、计划与顺序调度|workflow-core planner/ports、server run service|T4.3,T1.2|draft测试与版本运行均冻结快照；READY条件认领只执行一次；状态输出事件同事务|
|T5.2|条件、Parallel、Join边状态传播|workflow-core advance、test-fixtures|T5.1,T3.5|ENG-02/03/04/16通过；未激活边不阻塞，失败不冒充INACTIVE|
|T5.3|真实内置数据执行器与结果校验|node-catalog executors、白名单输入输出schema|T5.1|Transform/Template/Condition/Assert真实运算；非法类型、除零、超限产生准确错误|
|T5.4|有限并发、attempt重试、超时、取消与fencing|core scheduler/attempt、server生命周期|T5.2,T5.3|4全局/2每run/2接纳/20排队上限；1s/2s退避；晚到回调不覆写；5s终止收敛|
|T5.5|审批持久等待与原子决定|ApprovalRequest持久层、engine审批分支、决定API|T5.4|重复决定只一次有效；拒绝失败；等待不占节点槽；混合运行状态正确|

T5.2 与 T5.3 可在 T5.1 后分人实施，接口由node result契约隔开；T5.4 合并两者后再做，避免三人同时改调度循环。

## Phase 6：运行可视化与历史

|ID|任务与输入|修改边界及交付|依赖|独立验收/测试|
|---|---|---|---|---|
|T6.1|SSE持久游标与补发|server runtime-events、事件API集成测试|T5.1,T5.4|commit后发送、runId+seq去重、历史/live交接无缺口、慢客户端有界|
|T6.2|只读运行画布、节点尝试与历史详情|web/features/runs、runtime overlay store|T6.1,T3.1,T4.3|状态来源真实SSE；历史对原快照；日志可定位；不污染编辑图|
|T6.3|审批/取消UI与重启中断|web运行动作、server启动扫描、故障测试|T5.5,T6.2,T1.3|审批过期/重复明确提示；强制退出后所有未终态INTERRUPTED，无自动续跑|

## Phase 7：结果验证与失败闭环

|ID|任务与输入|修改边界及交付|依赖|独立验收/测试|
|---|---|---|---|---|
|T7.1|节点测试、TestCase/TestResult与diff|test API/持久层、web验证面板|T5.3,T6.2|仅允许4类单节点测试；冻结断言；期望/实际可读；断言失败不能run成功|
|T7.2|DB/SSE/竞态故障闭环|受影响server/core与隔离故障测试|T6.3,T7.1|TEST_STRATEGY的DB/EVT矩阵，重点commit失败不发成功、重连缺口补齐|
|T7.3|备份恢复、历史清理与设置|server maintenance、Node CLI、settings UI|T1.3,T4.3,T6.3|服务运行时维护锁拒绝；新目录恢复一致；清理不删活跃run、版本或当前草稿|
|T7.4|本机请求边界和资源限额|server session/Origin/Host、schema限额与UI错误|T6.1,T7.1|跨站写入拒绝；200/400/20图限额及JSON/事件限额明确；文本无脚本执行|

## Phase 8：可交付质量

|ID|任务与输入|修改边界及交付|依赖|独立验收/测试|
|---|---|---|---|---|
|T8.1|离线端到端与性能测量|tests/e2e、性能脚本、结果记录；只按测量改瓶颈|T7.1–T7.4,T3.4|两个完整离线输入路径，200节点指标采样，真实Win10/11生产构建|
|T8.2|可访问性、帮助与交付审查|web可访问性修复、用户文档、验收报告|T8.1|FR/NFR无漏项，键盘焦点和非颜色状态，已知限制清楚；所有本地核心检查可运行|

## 集成次序、风险和停止条件

最小纵向演示先走 T1→T2→T3.1/3.2→T4→T5.1/5.3 的直线流程；再加入复杂编辑和分支调度，最后补齐故障。演示顺序可以交错，阶段正式完成仍需满足路线图的全部验收项。

不得跳过的关口：T1.3 安装/SQLite版本；T4.2 统一revision；T5.2 分支Join；T5.4 fencing；T6.1 提交与事件；T7.2 数据库失败。任何关口若无法验证，应保留任务为未完成并解释阻塞，不能先展示假功能补齐页面。

变更超过任务边界时，先列“现有契约问题→最小修订→受影响文档与测试”；普通实现细节可自主判断，不为每个内部函数请求批准。新增功能优先级、部署依赖或执行语义变动应记录设计决策，不能悄悄扩展本轮/首版范围。
