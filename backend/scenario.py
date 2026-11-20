"""Scripted agent discussion for the local meeting-room demo."""

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

BACKEND = Path(__file__).resolve().parent
SCHEMA = BACKEND.parent / "tech/artifacts/schema/design-spec.schema.json"
SPEC = json.loads((BACKEND / "fixtures/meeting-discussion-spec.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
VALIDATOR.check_schema(VALIDATOR.schema)
IDEA = "做一个内部会议室预约工具，首版先解决查空闲和预约。"
ORIGINAL_CRITERION = "能识别并处理时段冲突。"
APPROVED_STAGE = 5

ROUNDS = (
    (
        ("user", "idea", IDEA, (), None),
        ("host", "agenda", "先确认员工如何查找空闲时段、提交预约和收到结果。请各角色明确首版范围及需要验证的风险。", (1,), None),
    ),
    (
        ("product", "opinion", "首版覆盖空闲查询、提交预约和查看确认结果。跨地点搜索、审批与日历同步留待后续讨论。", (1,), None),
        ("research", "opinion", "员工希望在安排会议时快速找到可用房间。案例先按一个办公地点讨论，实际使用频率仍需调研。", (1,), None),
        ("architect", "opinion", "查询到空闲只代表查询当下的状态。提交预约时，服务端需要重新检查同一房间的时段是否重叠。", (2,), None),
        ("designer", "opinion", "预约界面应明确区分确认成功、无可用时段和提交时发生冲突，并提供重新选择的入口。", (2,), None),
        ("market", "opinion", "内部工具首版无需定价。试用阶段可以记录预约成功率和冲突发生次数，帮助判断需求价值。", (1,), None),
    ),
    (
        ("critic", "challenge", "两位员工同时查到空闲并提交同一时段，会不会都预约成功？需要观察两个请求各自的结果。", (5,), "FR-02"),
        ("architect", "response", "将同一房间的冲突检查与写入放在同一受保护的事务内，避免两个请求基于同一空闲结果分别写入。", (8,), "FR-02"),
        ("designer", "response", "另一个请求收到明确的冲突提示，并可返回可用时段列表重新选择。", (8,), "FR-02"),
    ),
    (
        ("scribe", "draft", "已形成草稿 v1。FR-01 描述查询与预约，FR-02 记录同一会议室不得重复占用。", (3, 8, 9, 10), "FR-02"),
        ("reviewer", "revise", "退回草稿 v1。FR-02 只说能识别冲突，缺少并发测试的预期结果。请架构师和设计师补充可观测的验收条件。", (11,), "FR-02"),
    ),
    (
        ("architect", "response", "同时提交两个重叠时段的请求，检查同一会议室仅写入一条有效预约记录。", (12,), "FR-02"),
        ("designer", "response", "同时观察另一个请求的界面结果，确认显示明确的时段冲突提示。", (12,), "FR-02"),
        ("critic", "challenge", "复核同时满足两个条件：只有一条有效预约，另一位员工收到冲突提示。两个结果都要能观察到。", (13, 14), "FR-02"),
        ("scribe", "draft", "已修订草稿 v2。FR-02 沿用原 ID，补齐并发场景和两个请求各自的结果。", (12, 13, 14, 15), "FR-02"),
        ("reviewer", "pass", "草稿 v2 通过独立评审。FR-02 的并发条件和可观测结果均已明确，可以提交用户签收。", (16,), "FR-02"),
    ),
    (
        ("user", "confirmed", "已签收草稿 v2，可以导出设计规格并导入 FlowStudio。", (17,), None),
    ),
)


def scenario_events(stage, offset):
    return [
        dict(seq=offset + number, role=role, kind=kind, text=text, refs=refs, requirement=requirement)
        for number, (role, kind, text, refs, requirement) in enumerate(ROUNDS[stage], 1)
    ]


def scenario_draft(revision):
    content = deepcopy(SPEC)
    if revision == 1:
        content["requirements"][1]["acceptance_criteria"] = [ORIGINAL_CRITERION]
    VALIDATOR.validate(content)
    return {
        "revision": revision,
        "verdict": "REVISE" if revision == 1 else "PASS",
        "sources": {"FR-01": [1, 3, 6], "FR-02": [5, 8, 9, 10] if revision == 1 else [8, 12, 13, 14, 15]},
        "content": content,
    }
