# 任务分解数据与质量控制
## Task 严格结构
机器定义见 [TaskProposal Schema](../contracts/task-proposal.schema.json)；所有属性 required，只有 estimated_effort 可 null。数组上限见 Schema，任务总数 1–100。

| 字段 | 类型 / 校验 |
|---|---|
| task_id | string 1–64，字母起始，仅字母数字下划线横线，计划内唯一 |
| name / description | string 1–120 / 1–4000，trim 后非空 |
| category / stage / assigned_role | string 1–64，未设置枚举限制时接受非空标识；设置限制时须在允许集合内；角色只是建议 |
| priority | high / medium / low（区别需求 P0 和 must） |
| dependencies | task_id[]，唯一，无自己，均存在 |
| inputs | InputRef[]：kind=spec 时 {kind,requirement_id}；kind=task 时 {kind,task_id,deliverable_id} |
| deliverables | {id:string,text:string}[]，至少 1；id 在任务内唯一 |
| acceptance_criteria | string[]，至少 1，描述可观察结果 |
| source_requirement_ids | 需求 ID[]；引用必须存在；允许为空仅限有明确过程依据的任务 |
| rationale | string，空需求引用时必须非空，解释过程依据 |
| assumptions | {id,text,impact:low/critical}[]；全计划 ID 唯一 |
| estimated_effort | null 或 {min_hours,max_hours,basis,is_estimate:true}；0<min≤max，有依据 |

InputRef 是规划阶段的成果引用，非实际文件地址。未来运行适配再补 schema_id、artifact_key、json_pointer，不把尚不存在的成果当已生成。
TaskProposal={schema_version:"1.0",tasks:Task[],blocking_questions:string[]}；有阻断问题可 tasks=[]。任务合理性并非 JSON Schema 自动保证。

## 质量流水线
1. 严格解析 JSON/Schema；禁止未知字段、运行状态和脚本字段。
2. 结构：唯一 ID、依赖、阶段、角色、输入引用、交付 ID、DAG。
3. 覆盖：构建 requirement_id→task_ids。must 无引用为阻断；should/could 无引用为 warning。引用存在只是“已关联”，不等于实质完成。
4. 重复：名称标准化后完全相同或交付文本相同给 warning；相似语义由人工确认，不按相似度自动删任务。
5. 粒度：一个任务应有单一主要交付及可验收目标。跨多个无关领域或“完成整个产品”由人工标记过大；只改一个词无独立交付标过细。
6. 规划约束 required_categories 均需出现；过程任务需 rationale，不强制伪造需求 ID。
7. 模型修复携原始规格、候选和字段错误，调用数总计最多三次。即使修复成功仍需用户查看覆盖和假设。
8. 人工增加、删除、改依赖沿用相同验证器，不得给 UI 建立宽松旁路。

不以固定节点数要求不同规格产生一样计划；任务数量仅辅助观测。结构错误为硬失败，语义评审见测试标准。
