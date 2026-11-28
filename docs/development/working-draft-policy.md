# 初稿契约、模板与质量门禁

本模块实现阶段 2.2 的独立接口。现有 `InitialOutline` 和 `OutlineContent` 仍向工作台展示扁平结构的初始框架，并复用 `DraftRevision` 记录草稿发布。阶段 2.1 提出的逐项证据、稳定模块标识和关系校验由新版 `WorkingDraft` 表达。两种格式并存，默认开题、派发、成稿及评审顺序继续使用现有实现。

## 参考与采用方式

- GitHub Spec Kit 的默认规格模板区分必填用户场景、需求、成功标准和推断。Specify 在开题时加载 Constitution，并在草稿之后检查必填章节、测试性和范围。Clarify 按影响选题，答复后复核规格。Constitution 有自己的版本和修订规则，Preset 对模板和指令独立解析。这里采用可版本化的模板及策略，把可信硬约束和模板选择交给调用方。初稿问题上限按本项目本阶段要求默认为 3。 [规格模板](https://github.com/github/spec-kit/blob/6ba8beefff8d6408cadd62038e92d459093e33a8/templates/spec-template.md) · [Specify 检查清单](https://github.com/github/spec-kit/blob/6ba8beefff8d6408cadd62038e92d459093e33a8/templates/commands/specify.md#L148) · [Clarify](https://github.com/github/spec-kit/blob/6ba8beefff8d6408cadd62038e92d459093e33a8/templates/commands/clarify.md#L129) · [Constitution](https://github.com/github/spec-kit/blob/6ba8beefff8d6408cadd62038e92d459093e33a8/templates/commands/constitution.md#L75) · [Preset 解析顺序](https://github.github.com/spec-kit/presets/overview.html)
- OpenSpec 用 Schema 声明成果类型、生成说明和依赖。加载时检查 ID、引用和环，执行时按已完成的依赖计算可生成成果。这里把关系校验用于工作草稿里的模块层级和依赖，把流程步骤及触发场景的角色引用作为独立检查。 [工作流 Schema](https://github.com/Fission-AI/OpenSpec/blob/f1cd852f4edce22b8df9548a4df889172ca8ff25/schemas/spec-driven/schema.yaml) · [Schema 校验](https://github.com/Fission-AI/OpenSpec/blob/f1cd852f4edce22b8df9548a4df889172ca8ff25/src/core/artifact-graph/schema.ts) · [依赖图测试](https://github.com/Fission-AI/OpenSpec/blob/f1cd852f4edce22b8df9548a4df889172ca8ff25/test/core/artifact-graph/graph.test.ts)

## 接口与调用顺序

| 接口 | 用法 |
| --- | --- |
| `WorkingDraft` / `WorkingDraftContent` | Pydantic 模型。每次发布的 ID 由会话、输入版本和工作稿修订号生成。模块可以用 `working_module_id(session_id, stable_key)` 分配 ID，改名或跨修订时复用业务键和原 ID。 |
| `DraftTemplate` / `TemplateField` | 以 `ref` 指定 ID 与版本。按字段声明类型、必填性、说明、标题层级及 `non_empty`、`unique` 验收规则。核心字段使用契约类型，扩展字段用 `text` 或 `text_list` 并放入 `custom_sections`。 |
| `DraftPolicy.plan` | 返回版本化的生成计划。默认框架优先、整体概要、专业审查后置、输出预算 4096 Token。模板内容及约束优先级、澄清计划一并交给未来的生成器。 |
| `ClarificationPolicy.plan` | 接收尚未解决的问题和本输入版本的 `asked_ids`。只把 blocking 放入初稿前的提问队列，important 留待集中确认，deferred 交给后续讨论。限额默认 3。超过限额时返回剩余阻断项及暂停原因。 |
| `InitialDraftQualityGate.evaluate` | 校验工作稿与可信证据快照。返回状态、规则 ID、JSON Pointer、严重度、修复建议和发布判定。独立传入的 `SemanticFinding` 默认记为警告。 |
| `WorkingDraft.as_working_revision` | 生成 `working_schema_version=2`、`version=0`、`current=False` 的持久化包装，供以后写入工作草稿存储。正式候选稿另行冻结为 `DraftSpec`。签收门禁拒绝被误放进正式草稿集合的工作草稿。 |

计划先读入可信快照。应用负责筛出未被替代且当前会话可见的消息，确认引用只能来自已确认的消息。显式硬约束及已知问题由调用方从用户输入和企业规则传入。门禁核对硬约束原文、类型、规则引用和来源，检查已知阻断问题在生成过程中是否被遗漏或改类。它可以验证已提供的元数据，判断原始需求中尚未提取的约束及条目语义的准确性留给独立分析。

```python
from backend.initial_drafts.contracts import WorkingDraft, working_draft_id
from backend.initial_drafts.policy import DraftPolicy
from backend.initial_drafts.template import DEFAULT_SOFTWARE_TEMPLATE
from backend.initial_drafts.validation import EvidenceContext, InitialDraftQualityGate

template = DEFAULT_SOFTWARE_TEMPLATE
policy = DraftPolicy()
context = EvidenceContext.model_validate(trusted_snapshot)
plan = policy.plan(context.pending_questions, template=template, asked_ids=already_asked)

# Only build a draft after the caller has resolved the prerequisite questions.
if not plan.clarification.publication_blocked:
    candidate = {
        "session_id": context.session_id,
        "draft_id": working_draft_id(context.session_id, context.input_version, 1),
        "input_version": context.input_version,
        "revision": 1,
        "template_ref": template.ref,
        "policy_ref": policy.ref,
        "created_at": created_at,  # timezone-aware
        "source_seqs": cited_sequences,
        "content": supplied_outline,
    }
    result = InitialDraftQualityGate().evaluate(candidate, context, template=template, policy=policy)
    if result.can_publish:
        working_revision = WorkingDraft.model_validate(candidate).as_working_revision()
```

自定义配置使用 `DraftTemplate.model_validate(config)` 和 `DraftPolicy.model_validate(config)`。修改模板或策略后递增各自版本。需要在模板之外强制保留某个视图时，将其 ID 加入策略的 `required_views`。更换模板后，策略引用的视图仍须在新模板中定义。

门禁状态为 `PASS`、`PASS_WITH_WARNINGS`、`INVALID`、`BLOCKED`。存在结构错误时优先返回 `INVALID`。结构合法且存在未解决的阻断问题时返回 `BLOCKED`。普通细节、集中确认项和专业语义意见可随工作草稿发布。语义核对清单覆盖目标命中、模块适配和职责重叠、范围偏移、流程缺口、风险和系统边界。

本页描述阶段 2.2 的独立 Python 模块。阶段 2.3 已将默认模板、初稿门禁和输出 Token 预算接入 Host。无需迁移已有工作草稿。 [Host 草稿优先](host-draft-first.md)
