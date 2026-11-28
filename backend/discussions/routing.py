"""Deterministic routing for factual issues and repeated, unchanged work."""

from collections import defaultdict

from discussion_orchestration.enums import DivergenceKind, IssueStatus, Role
from discussion_orchestration.validation import has_new_user_evidence, issue_fingerprint, normalize_issue_text


# A capability is only a fallback hint. An eligible explicit owner wins.
CAPABILITIES = {
    Role.ARCHITECT: ("权限", "并发", "一致性", "接口", "事务", "安全", "concurrency", "authorization"),
    Role.DESIGNER: ("交互", "键盘", "界面", "空状态", "反馈", "无障碍", "accessibility", "keyboard"),
    Role.PRODUCT: ("首版范围", "优先级", "业务流程", "scope", "priority"),
    Role.RESEARCH: ("访谈", "用户样本", "样本量", "interview", "sample"),
    Role.BUSINESS: ("市场", "竞品", "定价", "pricing", "market"),
}


def fact_routes(issues, scope, messages, *, active):
    sources = {message.seq: message.role for message in messages}
    assignments = defaultdict(list)
    comparison = []
    for issue in issues:
        if issue.kind != DivergenceKind.FACT:
            continue
        cited = next((sources.get(seq) for seq in reversed(issue.source_seqs)
                      if sources.get(seq) in scope), None)
        owner = issue.assignee if issue.assignee in scope else None
        baseline = owner or cited or scope[0]
        text = f"{issue.summary} {issue.closure_condition or ''}".casefold()
        scores = {role: sum(tag in text for tag in CAPABILITIES.get(role, ())) for role in scope}
        best = max(scores.values())
        matches = [role for role in scope if scores[role] == best and best]
        inferred = matches[0] if len(matches) == 1 else None
        suggested = owner or inferred or baseline
        selected = suggested if active else baseline
        baseline_reason = "existing_owner" if owner else "source_author" if cited else "topic_scope"
        reason = "capability_match" if inferred and not owner else baseline_reason
        applied_reason = reason if active else baseline_reason
        assignments[selected.value].append(issue.issue_id)
        comparison.append({
            "issue_id": issue.issue_id, "baseline": baseline.value, "suggested": suggested.value,
            "selected": selected.value, "reason": reason, "applied_reason": applied_reason,
        })
    return dict(assignments), comparison


def _response_signature(response):
    return (normalize_issue_text(response.treatment), normalize_issue_text(response.verification))


def stalled_facts(session, issues):
    """Two matching completed responses without a changed treatment or independent new evidence."""
    history = defaultdict(list)
    for issue in session.issues.values():
        if (issue.input_version == session.input_version and issue.kind == DivergenceKind.FACT
                and issue.status in (IssueStatus.ANSWERED, IssueStatus.CLOSED) and issue.response):
            history[issue_fingerprint(issue)].append(issue)
    messages = {message.seq: message for message in session.messages}
    stalled = []
    for issue in issues:
        if issue.kind != DivergenceKind.FACT or issue.needs_user:
            continue
        previous = sorted(history[issue_fingerprint(issue)], key=lambda item: item.response_seq or 0)[-2:]
        if len(previous) < 2:
            continue
        first, latest = previous
        if _response_signature(first.response) != _response_signature(latest.response):
            continue
        earlier_sources = set(first.source_seqs) | set(first.response.source_seqs)
        later_sources = set(latest.source_seqs) | set(latest.response.source_seqs) | set(issue.source_seqs)
        after = first.response_seq or 0
        if has_new_user_evidence(messages, later_sources - earlier_sources, after):
            continue
        stalled.append(issue)
    return stalled
