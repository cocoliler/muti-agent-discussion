# Design Spec 与通用规划参数
## 规格输入契约
实体 Project → DesignSpec → SpecRevision。一个项目当前一个主规格；未来可多个。SpecRevision 即 DesignSpecVersion，不另设表。
机器可读定义见[生成的 Schema](../tech/artifacts/schema/design-spec.schema.json)。显式演示入口独立于正式规划。所有写接口拒绝未知字段，字符串 trim 后检查非空。

| 字段 | 类型与限制 | 含义 |
|---|---|---|
| schema_version | const "1.0" | 导入格式版本 |
| product_name | string 1–120 | 产品名仅作为背景，不路由固定任务 |
| background | string 1–4000 | 业务背景 |
| goals / target_users | string[]，1–20 | 目标与用户 |
| requirements | Requirement[]，1–200 | 功能与非功能统一追踪 |
| technical_constraints / development_constraints | string[]，0–30 | 技术及人员/时间约束 |
| delivery_requirements / acceptance_criteria | string[]，1–30 | 交付及整体验收 |
| supplemental_text | string，0–12000 | 补充原文，不自动覆盖结构化字段 |
| confirmed_assumptions | string[]，0–30 | 用户确认后的假设原文 |

Requirement={id: string 1–64，pattern ^[A-Za-z][A-Za-z0-9_-]*$; kind: functional/nonfunctional; text: 1–2000; priority: must/should/could; acceptance_criteria: string[1..20]}。ID 在同一版本唯一，修改正文保留 ID，新增需求新 ID；删除需求不能让旧计划引用漂移。
提交规范化前后均限制请求体 1 MiB；长度按 Unicode 字符计，禁止控制字符，换行可保留。无文件路径、代码执行和密钥字段。

## 编辑和质量
1. 空表单可以作为浏览器本地草稿；保存正式 SpecRevision 需通过结构校验。
2. 导入仅接受 UTF-8 JSON，先预览字段差异再保存；拒绝重复 JSON key、不支持的版本和超限。
3. 确定性检查必填、ID 唯一、验收项；自然语言模糊/冲突用建议问题和人工审查，不宣称规则能理解全部矛盾。
4. 关键冲突（例如同时要求“完全离线”与“只用云 API”）由 PM 返回 blocking_questions，阻止形成正式计划。用户补充后保存新规格并发起新 PlanningRun。
5. 非关键假设随候选显示；确认保存必须提交确认的 assumption ID。用户拒绝则修改规格重规划或人工改计划，不自动补业务功能。
6. 重新规划不覆盖旧版。当前规格变更只使旧版来源显示 outdated，不改变旧版 validity。

## 通用规划参数（无预设模板）
PlanningConfig={model:string, granularity:coarse/standard/fine, planning_constraints:string[], policy?:PlanningPolicy}。
PlanningPolicy={schema_version:"1.0",rules_version:"1.0",allowed_stages:string[]|null,allowed_roles:string[]|null,allowed_categories:string[]|null,allowed_stage_edges:{from,to}[]|null,required_categories:string[]}。
policy省略时使用通用规则：字段合法、引用完整、DAG、需求覆盖；所有allowed_*为null，required_categories为空，不内置业务阶段、角色链或具体任务。
null表示不额外限制，非null空数组表示不允许任何值，若配置导致无法生成任务则报POLICY_UNSATISFIABLE。同阶段依赖仍允许；跨阶段在设置allowed_stage_edges后检查允许集合。
PM依据用户规格提出stage/category/assigned_role，均为稳定非空标识；前端展示原标签，未知标签使用中性色。可选policy允许用户约束角色和过程，但当前入口不要求填写。
粒度建议coarse/standard/fine为不同任务拆分详细度，不强制固定数量，硬上限100。
完整解析后的配置快照进入PlanningRun/PlanRevision。Prompt/规则版本随记录保存，旧历史不被新配置覆盖。
未来可增加WorkflowTemplate.resolve(id,version)→PlanningPolicy适配器；本轮只保留此扩展边界，不建立模板库、模板接口或预设模板文件。

## 后续输入
Markdown、PDF、Word 仅在 P2 作为解析器输出本契约的候选；解析器必须附缺失信息和来源段落，用户确认后保存，不能绕过同一校验。
