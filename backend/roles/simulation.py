"""Deterministic discussion roles for offline development and integration tests."""

import json
import os
import re
from copy import deepcopy

from backend.initial_drafts.contracts import working_module_id
from .contracts import RoleRequest
from .provider import Completion, ProviderError


def _field(text, labels):
    match = re.search(r"(?:^|[。；;\n])\s*(?:" + labels + r")\s*[:：]\s*([^。；;\n]+)", text, re.I)
    return match[1].strip()[:120] if match else ""


def _goal(req):
    return _field(req.user_input, "目标|goal") or req.user_input.strip().splitlines()[0][:120]


def _latest(req, roles):
    return max((m for m in req.messages if m.role in roles), key=lambda m: m.seq, default=None)


def _sources(message):
    return [message.seq] if message else []


def _scoring(req):
    """权衡分歧的模拟评分：固定两个方案，让流程可以离线跑通。"""
    message = _latest(req, {"critic", "scribe", "product"}) or _latest(req, {"user"})
    topic = (req.topic or "").strip() or _goal(req)
    options = [
        {"option_id": "A", "title": f"方案 A：{topic[:60]}",
         "scores": {"user_value": 5, "feasibility": 4, "cost": 3, "differentiation": 3, "risk": 3}},
        {"option_id": "B", "title": "方案 B：延后到下一版本，先交付最小可用范围",
         "scores": {"user_value": 3, "feasibility": 5, "cost": 5, "differentiation": 2, "risk": 4}},
    ]
    return dict(text="权衡分歧按五个维度打分，采纳综合得分更高的方案。", action="SCORE",
                topic_id=req.topic_id, target_roles=[], issue_ids=[], questions=[],
                source_seqs=_sources(message),
                scorecard={"options": options, "rationale": "优先保证用户价值，同时控制成本与风险。"})


def _host(req):
    if req.kind in ("integration", "draft"):
        return _scribe(req)
    if req.kind == "scoring":
        return _scoring(req)
    goal = _goal(req)
    blocking = [i for i in req.issues if i.severity == "blocking" and not i.treatment]
    owners = [r for r in req.allowed_roles if any(i.owner_role == r for i in blocking)]
    questions = []
    targets = []
    clarify = [i for i in blocking if i.owner_role not in req.allowed_roles
               or i.issue_id == "AUDIENCE" or i.issue_id == f"SOURCE-{i.target_item}"]
    if clarify:
        action = "ASK_USER"
        questions = [f"关于「{i.target_item}」，请补充：{i.closure_condition}" for i in clarify]
        reason = "有待确认的问题，需要补充信息。"
    elif owners:
        action, targets = "DISCUSS", owners
        reason = "请相关角色针对未关闭的问题给出验证办法。"
    elif req.draft:
        action = "SUBMIT_REVIEW"
        reason = f"候选稿 v{req.draft.revision} 已形成，请独立评审核查。"
    elif req.allowed_roles:
        action, targets = "DISCUSS", list(req.allowed_roles)
        reason = "请围绕首版用户、操作和验收条件发言。"
    else:
        action, questions = "ASK_USER", [f"谁能确认「{goal}」的首版范围？"]
        reason = "需要确认参与讨论的角色。"
    result = dict(text=f"本轮议题为「{req.topic or goal}」。{reason}", action=action, topic_id=req.topic_id,
                  target_roles=targets, issue_ids=[i.issue_id for i in blocking], questions=questions,
                  source_seqs=_sources(_latest(req, {"user", "scribe", "reviewer"})))
    if req.kind == "agenda" and action == "DISCUSS":
        audience = _field(req.user_input, "目标用户|用户|audience|users")
        scope = _field(req.user_input, "约束|scope|constraints")
        result["initial_outline"] = {
            "product_name": _field(req.user_input, "项目名称|名称|name") or goal,
            "positioning": f"围绕「{goal}」提供一条可试用的首版业务流程。",
            "target_users": [audience or "直接办理这项事务的使用者（角色待确认）"],
            "core_modules": ["业务操作入口", "业务规则校验", "结果反馈"],
            "business_flow": ["进入操作入口", f"提交「{goal}」所需的信息", "校验后显示处理结果"],
            "system_boundary": [scope or "首版覆盖单一核心场景，扩展场景留待确认。"],
            "goals": [goal],
            "scenarios": [f"{audience or '暂定使用者'}发起操作并查看结果。"],
            "scope_in": [goal],
            "module_relations": ["操作入口提交信息给规则校验，校验结果交给反馈模块。"],
            "constraints": [scope] if scope else [],
            "assumptions": ["暂按最小业务闭环组织功能。"],
            "open_questions": ([] if audience else ["首版最常使用此功能的是谁？"])
                + ([] if scope else ["有哪些场景或范围应明确留到后续版本？"]),
        }
    return result


ADVICE = {
    "product": "建议首版覆盖「{}」的主流程。用试用前后的完成时间衡量收益，比较人工处理与自动化成本，再决定扩展范围。",
    "research": "围绕「{}」，建议请实际使用者演示现有操作，并记录最容易中断的一步。",
    "architect": "建议对「{}」使用一组有效输入和一组无效输入检查边界；记录输出，并确认重试后的状态。",
    "designer": "建议请实际使用者演示「{}」，核对最容易中断的一步；据此设计成功、空状态和失败后的操作入口。",
    "market": "建议围绕「{}」比较试用前后的任务完成时间，用试用记录验证价值。",
}


def _initial_working_draft(req):
    legacy = _host(req.model_copy(update={"kind": "agenda"}))
    origin = req.messages[-1].seq
    if req.user_input.strip() in {"想做个工具", "做个工具", "做一个应用", "build an app"}:
        return {
            "action": "ASK_USER", "text": "需要先明确目标。", "source_seqs": [origin],
            "questions": [{"question_id": "core-goal", "question": "首版要解决哪个业务问题？", "category": "blocking",
                           "reason": "无法从输入判断产品目标。", "source_seqs": [origin]}],
        }
    flat = legacy["initial_outline"]

    def item(identifier, text, **fields):
        return {"id": identifier, "text": text, "status": fields.pop("status", "proposed"), **fields}

    modules = [item(working_module_id(req.session_id, key), title, name=title)
               for key, title in zip(("entry", "rules", "result"), flat["core_modules"])]
    for previous, current in zip(modules, modules[1:]):
        current["depends_on"] = [previous["id"]]
    constraints = req.initial_task.evidence.required_constraints
    sources = sorted({origin} | {seq for constraint in constraints for seq in constraint.source_seqs})
    content = {
        "product_name": flat["product_name"], "positioning": item("position", flat["positioning"]),
        "goals": [item("goal", flat["goals"][0])],
        "actors": [item("actor", flat["target_users"][0], status="assumed")],
        "scenarios": [item("scenario", flat["scenarios"][0], actor_ids=["actor"],
                           trigger="用户开始操作", outcome="用户收到处理结果")],
        "modules": modules,
        "business_flows": [item("flow", "主流程", steps=[
            item(f"step-{index}", action, actor_id="actor", module_id=module["id"], outcome=module["name"])
            for index, (action, module) in enumerate(zip(flat["business_flow"], modules), 1)
        ])],
        "scope_in": [item("scope", flat["scope_in"][0])],
        "boundaries": [item("boundary", flat["system_boundary"][0])],
        "constraints": [constraint.model_dump(mode="json") for constraint in constraints],
        "assumptions": [item("assumption", flat["assumptions"][0], status="assumed")],
        "open_questions": [
            {"question_id": f"open-{index}", "question": question, "category": "important",
             "reason": "可能影响后续范围", "source_seqs": [origin]}
            for index, question in enumerate(flat["open_questions"], 1)
        ],
    }
    return {"action": "CREATE_OUTLINE", "text": legacy["text"], "source_seqs": sources, "content": content,
            "resolved_questions": [{"question_id": question.question_id, "reason": "用户已补充初始目标。", "source_seqs": [origin]}
                                   for question in req.initial_task.previous_questions],
            "target_roles": legacy["target_roles"], "discussion_focus": "核对设计框架与主要业务边界"}


def _specialist(req):
    goal = _goal(req)
    message = _latest(req, {"user", "critic", "reviewer"})
    text, basis = ADVICE[req.role].format(goal), "assumption"
    if req.role == "market" and re.search(r"内部工具|内部使用|internal tool", req.user_input, re.I):
        text, basis = f"「{goal}」面向内部使用，本轮不讨论外部定价；可通过试用记录比较完成时间。", "not_applicable"
    directed = [i for i in req.issues if i.owner_role == req.role] if req.kind == "response" else []
    if directed:
        text += " 本轮验证：" + "；".join(f"{i.issue_id}（{i.target_item}）：{i.closure_condition}" for i in directed)
    elif message and message.role == "critic":
        text += f" 建议把质疑「{message.text[:160]}」作为一次单独的边界测试。"
    elif req.topic:
        text += f" 本轮重点核对「{req.topic}」。"
    result = dict(text=text, basis=basis, source_seqs=_sources(message))
    if req.kind == "stance":
        result["stances"] = [
            {"option_id": option.option_id, "position": "conditional",
             "reason": ADVICE[req.role].format(option.title), "source_seqs": _sources(message)}
            for option in req.candidate_options
        ]
    if directed:
        result["verification"] = "；".join(issue.closure_condition for issue in directed)
        result["responses"] = [
            {"issue_id": issue.issue_id, "treatment": issue.closure_condition,
             "verification": issue.closure_condition, "source_seqs": _sources(message)}
            for issue in directed
        ]
    return result


def _critic(req):
    message = _latest(req, req.allowed_roles) or _latest(req, {"user"})
    if not message or not req.allowed_roles:
        raise ProviderError("insufficient_context")
    earlier = _latest(req, {"critic"})
    if req.round > 1 and earlier and message.seq > earlier.seq and (message.analysis or {}).get("verification"):
        return dict(text="已核对本轮补充的处理方式，无新增异议。", source_seqs=[message.seq], no_new_issues=True)
    target = message.role if message.role in req.allowed_roles else req.allowed_roles[0]
    divergence = os.environ.get("DISCUSSION_SIMULATE_DIVERGENCE", "").strip().lower()
    return dict(text=f"针对消息 {message.seq} 的「{message.text[:160]}」，输入不完整时用户会看到什么结果？",
                source_seqs=[message.seq], target_roles=[target],
                divergence=divergence if divergence in ("fact", "value", "tradeoff") else "fact",
                verification=f"针对「{_goal(req)}」分别提交有效输入和缺少必填项的输入，记录两次输出及保存状态。")


def _audience(text):
    field = _field(text, "目标用户|用户|users|target users")
    if not field:
        answer = re.search(r"(?:^|\n)\d+\. [^\n]*由谁使用[？?]\n回答[：:][ \t]*([^\n]+)", text)
        if answer:
            value = answer[1].strip()
            field = (_field(value, "目标用户|用户|users|target users") or value) if value != "待确认" else ""
    return [s.strip() for s in re.split(r"[、,，]", field) if s.strip()][:20]


def _new_draft(req):
    goal = _goal(req)
    source = max((m for m in req.messages if m.role == "user" and goal in m.text),
                 key=lambda m: m.seq, default=None)
    if source is None:
        source = _latest(req, {"product"}) or _latest(req, {"user"})
        if source:
            goal = source.text[:120]
    if not source:
        raise ProviderError("insufficient_context")
    idea = req.user_input
    audience_message = _audience_evidence(req)
    users = _audience(audience_message.text) if audience_message else _audience(idea)
    constraint = _field(idea, "约束|constraint")
    spec = dict(schema_version="1.0", product_name=_field(idea, "项目名称|名称|project|name") or goal,
                background=idea[:4000], goals=[goal], target_users=users or ["目标用户待确认"],
                requirements=[dict(id="FR-01", kind="functional", priority="must", text=goal,
                                   acceptance_criteria=[f"完成目标：{goal}"])],
                technical_constraints=[], development_constraints=[constraint] if constraint else [],
                delivery_requirements=["交付可演示的首版主流程。"], acceptance_criteria=["逐项演示首版需求并核对结果。"],
                supplemental_text="", confirmed_assumptions=[])
    mapping = {"FR-01": [source.seq]}
    for agreement in dict.fromkeys(req.consensus):
        evidence = [m.seq for m in req.messages if agreement in m.text]
        if evidence and agreement != goal and len(mapping) < 200:
            key = f"FR-{len(mapping) + 1:02d}"
            mapping[key] = sorted(evidence)
            spec["requirements"].append(dict(id=key, kind="functional", priority="should", text=agreement[:2000],
                                             acceptance_criteria=[f"完成目标：{agreement[:120]}"]))
    return spec, mapping


def _criterion(item):
    if re.search(r"不得重叠|禁止重复|不得重复|避免重复|只允许一次|prevent duplicates", item["text"], re.I):
        return f"针对「{item['text'][:120]}」，同时提交两个互斥请求，只生成一个有效结果，另一请求返回冲突提示。"
    return f"给定测试用例中的有效输入，执行「{item['text'][:120]}」后，返回结果与用例中的预期值一致。"


def _has_observation(criteria):
    # The simulator recognizes a trigger and an observable outcome. A human
    # reviewer or model can handle criteria outside this limited vocabulary.
    trigger = r"给定|输入|提交|选择|尝试|同时|当|时[，,]|后[，,]|若|如果|\b(when|given|after|if|upon)\b"
    outcome = r"返回|显示|生成|保存|记录|收到|提示|拒绝|等于|一致|\b(returns?|shows?|displays?|records?|rejects?|receives?|creates?|equals?)\b"
    return any(not c.startswith("完成目标：") and re.search(trigger, c, re.I) and re.search(outcome, c, re.I)
               for c in criteria)


def _treatment_refs(req, issue):
    return [m.seq for m in req.messages if m.seq in issue.source_seqs and issue.treatment
            and issue.treatment in m.text and m.role in {issue.owner_role, "user"}]


def _audience_evidence(req):
    return max((m for m in req.messages if m.role == "user" and _audience(m.text)),
               key=lambda m: m.seq, default=None)


def _terms(text):
    words = set(re.findall(r"[a-z0-9]+", text.casefold()))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words - {"and", "the", "user", "with", "for", "can", "用户", "需要", "系统", "可以", "功能", "首版"}


def _supporting_messages(req, item, allowed):
    terms = _terms(item["text"])
    return [m.seq for m in req.messages if m.seq in allowed
            and (item["text"] in m.text or terms.intersection(_terms(m.text)))]


def _scribe(req):
    critic = _latest(req, {"critic"})
    risks = [critic.text] if critic and (critic.analysis or {}).get("challenge_result") != "no_new_issues" else []
    if req.kind == "integration":
        if not req.messages:
            raise ProviderError("insufficient_context")
        return dict(text=f"已整理「{_goal(req)}」的讨论结论与待验证问题。",
                    source_seqs=[m.seq for m in req.messages], agreements=list(req.consensus),
                    alternatives=[], disagreements=[issue.description for issue in req.issues if not issue.treatment],
                    assumptions=[], risks=risks)
    if req.draft:
        spec, mapping = deepcopy(req.draft.content), deepcopy(req.draft.source_map)
    else:
        spec, mapping = _new_draft(req)
    items = {i["id"]: i for i in spec["requirements"]}
    pending = []
    supporting_seqs = set()
    for issue in req.issues:
        item = items.get(issue.target_item)
        treatment_refs = _treatment_refs(req, issue)
        if item and issue.issue_id == f"SOURCE-{item['id']}":
            evidence = _supporting_messages(req, item, {m.seq for m in req.messages if m.role == "user"})
            if evidence:
                mapping[item["id"]] = sorted(set(mapping[item["id"]]).union(evidence))
                continue
        if issue.target_item == "target_users" and (answer := _audience_evidence(req)):
            spec["target_users"] = _audience(answer.text)
            supporting_seqs.add(answer.seq)
            continue
        # Accept a simulator-generated criterion or a treatment that the issue
        # explicitly ties to a visible contributor message.
        if item and issue.closure_condition == _criterion(item):
            criterion = issue.closure_condition
        elif item and treatment_refs and _has_observation([issue.treatment]):
            criterion = issue.treatment
        else:
            pending.append(issue.description)
            continue
        criteria = item["acceptance_criteria"]
        if criterion[:2000] not in criteria:
            if len(criteria) >= 20:
                pending.append(issue.description)
                continue
            criteria.append(criterion[:2000])
        for seq in treatment_refs:
            if seq not in mapping[issue.target_item]:
                mapping[issue.target_item].append(seq)
    supporting_seqs.update(_sources(critic))
    cited = sorted(supporting_seqs.union(seq for seqs in mapping.values() for seq in seqs))
    changes = [
        {"previous_id": identifier, "replacement_ids": [], "reason": "按当前用户输入重建规格，此需求未沿用。"}
        for identifier in req.previous_requirement_ids if identifier not in mapping
    ]
    return dict(text=f"已整理「{spec['product_name']}」的 {len(items)} 条需求及对应来源。",
                source_seqs=cited, content=spec, source_map=mapping, agreements=list(req.consensus),
                alternatives=[], disagreements=pending, assumptions=[], risks=risks,
                requirement_changes=changes)


def _review(req):
    draft = req.draft
    items = {i["id"]: i for i in draft.content["requirements"]}
    refs = sorted({seq for seqs in draft.source_map.values() for seq in seqs})
    findings, questions = [], []
    for issue in req.issues:
        target = issue.target_item
        value = items[target]["acceptance_criteria"] if target in items else draft.content.get(target)
        if value is None:
            raise ProviderError("insufficient_context")
        values = value if isinstance(value, list) else [value]
        sources = list(draft.source_map.get(target, refs))
        resolved = issue.closure_condition in values
        if issue.issue_id == "AUDIENCE" and target == "target_users":
            answer = _audience_evidence(req)
            resolved = bool(answer and _audience(answer.text) == values)
            if resolved and answer.seq not in sources:
                sources.append(answer.seq)
        if target in items and issue.issue_id == f"SOURCE-{target}":
            resolved = bool(_supporting_messages(req, items[target], sources))
        if target in items and issue.treatment in values:
            resolved |= bool(set(_treatment_refs(req, issue)).intersection(sources))
        if resolved:
            resolution = issue.treatment if issue.treatment in values else issue.closure_condition
            if target == "target_users":
                resolution = "、".join(values)
            elif target in items and issue.issue_id == f"SOURCE-{target}":
                resolution = f"相关来源消息 {_supporting_messages(req, items[target], sources)}"
            detail = f"复核 {target}，已记录：{resolution}。"
        else:
            detail = issue.description
        findings.append(dict(issue_id=issue.issue_id, severity=issue.severity, target_item=target,
                             description=detail, owner_role=issue.owner_role, closure_condition=issue.closure_condition,
                             source_seqs=sources, status="closed" if resolved else "open"))
    for target, item in items.items():
        source_id = f"SOURCE-{target}"
        source_refs = draft.source_map[target]
        if not _supporting_messages(req, item, source_refs):
            questions.append(f"哪条原始需求或讨论消息可以支持「{item['text'][:120]}」？")
            if not any(f["issue_id"] == source_id for f in findings):
                findings.append(dict(issue_id=source_id, severity="blocking", target_item=target,
                                     description=f"{target} 与所引消息没有可识别的共同用语，需要确认依据。",
                                     owner_role="designer", closure_condition="补充与条目内容对应的来源消息。",
                                     source_seqs=source_refs, status="open"))
        issue_id = f"AC-{target}"
        if _has_observation(item["acceptance_criteria"]) or any(f["issue_id"] == issue_id for f in findings):
            continue
        findings.append(dict(issue_id=issue_id, severity="blocking", target_item=target,
                             description=f"{target} 的验收条件没有同时写明测试输入和可观察结果。",
                             owner_role="product", closure_condition=_criterion(item),
                             source_seqs=draft.source_map[target], status="open"))
    if draft.content["target_users"] == ["目标用户待确认"]:
        questions.append(f"首版「{draft.content['product_name']}」由谁使用？")
        if not any(f["target_item"] == "target_users" and f["status"] == "open" for f in findings):
            findings.append(dict(issue_id="AUDIENCE", severity="blocking", target_item="target_users",
                                 description="草稿中的使用者仍待确认。", owner_role="designer",
                                 closure_condition="由用户明确首版使用者并写入 target_users。",
                                 source_seqs=refs, status="open"))
    blocking = any(f["status"] == "open" and f["severity"] == "blocking" for f in findings)
    verdict = "NEEDS_INPUT" if questions else "REVISE" if blocking else "PASS"
    return dict(text=f"已核查候选稿 v{draft.revision}，结论为 {verdict}。", source_seqs=refs,
                draft_id=draft.id, verdict=verdict, findings=findings, questions=questions)


class SimulationProvider:
    async def complete(self, request: RoleRequest, messages: list[dict]) -> Completion:
        if request.kind == "initial_outline":
            return Completion(json.dumps(_initial_working_draft(request), ensure_ascii=False), usage={"total_tokens": 0})
        handler = {"host": _host, "critic": _critic, "scribe": _scribe, "reviewer": _review}.get(request.role, _specialist)
        return Completion(json.dumps(handler(request), ensure_ascii=False), usage={"total_tokens": 0})
