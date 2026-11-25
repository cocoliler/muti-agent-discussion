"""Drive discussion roles until the engine needs a user decision."""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from discussion_orchestration import (
    DivergenceKind,
    MessageKind,
    Role,
    SessionStatus,
    TaskKind,
    WaitingFor,
)
from spec_handoff.draft import DraftSpec

from backend.roles.contracts import CandidateOption, DraftSnapshot, PriorIssue, RoleRequest, SourceMessage
from backend.roles.provider import DeepSeekProvider
from backend.roles.runner import RoleRunner
from backend.roles.simulation import SimulationProvider
from .errors import ApiError
from .repository import LeaseLost, VersionConflict

if TYPE_CHECKING:
    from .application import DiscussionService

logger = logging.getLogger(__name__)
ROLE_ALIASES = {"business": "market", "challenger": "critic", "recorder": "scribe"}
ENGINE_ROLES = {alias: role for role, alias in ROLE_ALIASES.items()}


@dataclass(frozen=True)
class Job:
    request: RoleRequest
    role: str
    kind: str
    snapshot_ref: str
    task: object | None = None


class AgentExecution:
    LEASE_SECONDS = 120
    MAX_ACTIVE_SESSIONS = 4

    def __init__(self) -> None:
        self.mode = os.environ.get("DISCUSSION_AGENT_MODE", "simulation").strip().lower()
        if self.mode not in ("simulation", "real", "off"):
            raise ValueError("DISCUSSION_AGENT_MODE must be simulation, real, or off")
        self.active: set[str] = set()
        self.session_slots = asyncio.Semaphore(self.MAX_ACTIVE_SESSIONS)

    async def recover(self, service: "DiscussionService") -> None:
        if self.mode == "off":
            return
        async with asyncio.TaskGroup() as tasks:
            while True:
                try:
                    capacity = self.MAX_ACTIVE_SESSIONS - len(self.active)
                    if capacity > 0:
                        for session_id in service.repo.pending_runs(self.mode, limit=capacity):
                            if session_id not in self.active:
                                tasks.create_task(self.run(service, session_id))
                except Exception:
                    logger.exception("Could not check pending discussions")
                await asyncio.sleep(1)

    async def run(self, service: "DiscussionService", session_id: str) -> None:
        if self.mode == "off" or session_id in self.active:
            return
        self.active.add(session_id)
        owner = uuid4().hex
        try:
            async with self.session_slots:
                # Every batch renews the lease before starting provider calls. Two
                # per-task timeouts (2 × 45s) fit within the lease.
                while (generation := service.repo.claim_run(session_id, self.mode, owner, self.LEASE_SECONDS)) is not None:
                    finished = False
                    try:
                        await self._drive(service, session_id, owner)
                        finished = True
                    except LeaseLost:
                        return
                    except Exception:
                        logger.exception("Automatic role execution failed for discussion %s", session_id)
                        finished = True
                    finally:
                        service.repo.release_run(session_id, owner, generation, finished=finished)
        except Exception:
            logger.exception("Could not claim or release discussion %s", session_id)
        finally:
            self.active.discard(session_id)

    async def _drive(self, service: "DiscussionService", session_id: str, owner: str) -> None:
        session, _ = service._load(session_id)
        jobs = self._next_jobs(service, session, owner)
        if not jobs:
            return
        try:
            provider = (DeepSeekProvider(api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
                                         model=os.environ.get("DISCUSSION_MODEL", "deepseek-flash").strip(), max_tokens=8192)
                        if self.mode == "real" else SimulationProvider())
        except ValueError:
            for job in jobs:
                number, _ = self._start(service, job, owner)
                self._finish(service, job, owner, number, "FAILED", error={"code": "PROVIDER_CONFIG"})
            return

        runner = RoleRunner(session_id, provider, mode=self.mode)
        while jobs:
            service.repo.renew_run(session_id, owner, self.LEASE_SECONDS)
            # Keep each task and its retry in one batch. Recompute pending jobs from
            # committed state so rolling over cannot replay successful tasks.
            if runner.call_limit - runner.calls_used < runner.MAX_ATTEMPTS:
                runner = RoleRunner(session_id, provider, mode=self.mode)
            slots = (runner.call_limit - runner.calls_used) // runner.MAX_ATTEMPTS
            jobs = jobs[:slots]
            starts = [self._start(service, job, owner) for job in jobs]
            results = await asyncio.gather(*(runner.run(job.request, validation_diagnostic=diagnostic)
                                             for job, (_, diagnostic) in zip(jobs, starts)), return_exceptions=True)
            failed = False
            for job, (number, _), result in zip(jobs, starts, results):
                if isinstance(result, BaseException):
                    self._finish(service, job, owner, number, "FAILED", error={"code": "AGENT_EXECUTION_FAILED"})
                    failed = True
                    continue
                applied = False
                error = result.error
                if result.output is not None:
                    try:
                        applied = self._apply(service, job, result.output, owner)
                    except (ApiError, VersionConflict) as exc:
                        error = getattr(exc, "code", "REVISION_CONFLICT")
                for index, attempt in enumerate(result.attempts):
                    if index:
                        self._record(service, job, owner, number + index)
                    last = index == len(result.attempts) - 1
                    code = error if last else attempt.error
                    status = "FAILED" if code else "SUCCEEDED" if applied else "IGNORED"
                    failure = {"code": code} if code else None
                    if code == "invalid_output" and attempt.diagnostic:
                        failure["diagnostic"] = attempt.diagnostic
                    self._finish(service, job, owner, number + index, status, usage=attempt.usage,
                                 error=failure, elapsed_seconds=attempt.elapsed_seconds)
                if not result.attempts:
                    self._finish(service, job, owner, number, "FAILED", error={"code": error or "NO_OUTPUT"})
                failed |= bool(error)
            if failed:
                return
            session, _ = service._load(session_id)
            if session.input_version != jobs[0].request.input_version:
                return  # A substantive message schedules execution for the new input.
            next_jobs = self._next_jobs(service, session, owner)
            still_pending = {job.request.logical_task_id for job in next_jobs}
            repeated = [
                (job, number, result) for job, (number, _), result in zip(jobs, starts, results)
                if job.request.logical_task_id in still_pending and not isinstance(result, BaseException)
            ]
            if repeated:
                for job, number, result in repeated:
                    last = result.attempts[-1] if result.attempts else None
                    self._finish(
                        service, job, owner, number + max(0, len(result.attempts) - 1), "FAILED",
                        usage=last.usage if last else None, elapsed_seconds=last.elapsed_seconds if last else None,
                        error={"code": "AGENT_NO_PROGRESS"},
                    )
                return
            jobs = next_jobs

    def _next_jobs(self, service, session, owner) -> list[Job]:
        if (session.status == SessionStatus.DISCUSSING and session.waiting_for == WaitingFor.HOST_DECISION
                and not session.tasks and session.current_item() is not None):
            item = session.current_item()
            issues = session.open_issues(item.topic_id)
            if any(issue.needs_user for issue in issues):
                issues = [issue for issue in issues if issue.needs_user]
                questions = list(dict.fromkeys(
                    issue.user_question
                    or (issue.summary if len(issue.summary) <= 200 and "\n" not in issue.summary else
                        f"关于「{item.title}」，首版应优先满足哪些业务约束？请说明你的取舍。")
                    for issue in issues
                ))
                proposal = {"action": "ask_user", "questions": questions,
                            "issue_ids": [issue.issue_id for issue in issues]}
            elif issues:
                # 权衡分歧不能点名争论，交给引擎的收敛打分；只有事实分歧才点名回应。
                assignable = [issue for issue in issues if issue.kind != DivergenceKind.TRADEOFF]
                if not assignable:
                    items = session.agenda.items
                    proposal = {"action": "submit_draft" if item == items[-1] else "next_topic"}
                else:
                    assignments = {}
                    sources = {message.seq: message.role for message in session.messages}
                    for issue in assignable:
                        role = issue.assignee if issue.assignee in item.role_scope else next((sources.get(seq) for seq in reversed(issue.source_seqs)
                                                       if sources.get(seq) in item.role_scope), item.role_scope[0])
                        assignments.setdefault(role.value, []).append(issue.issue_id)
                    proposal = {"action": "name_responses", "assignments": assignments}
            else:
                items = session.agenda.items
                proposal = {"action": "submit_draft" if item == items[-1] else "next_topic"}
            if self._commit(service, session.session_id, "propose", proposal,
                            lambda current: current.status == session.status and current.waiting_for == session.waiting_for
                            and current.last_seq == session.last_seq and current.input_version == session.input_version,
                            owner=owner, allow_rejection=True):
                session, _ = service._load(session.session_id)
        return self._jobs(session)

    def _jobs(self, session) -> list[Job]:
        replaced = {m.replaces_answer_seq for m in session.messages if m.replaces_answer_seq is not None}
        history = [m for m in session.messages if m.seq not in replaced and m.substantive
                   and (not replaced or m.role == Role.USER or m.input_version == session.input_version)]
        user_messages = [m for m in history if m.kind == MessageKind.USER_INPUT and m.text.strip()]
        if not user_messages:
            return []
        idea = "\n".join(m.text for m in user_messages)
        if session.status == SessionStatus.CLARIFYING and session.waiting_for == WaitingFor.HOST_AGENDA:
            return [self._job(session, user_messages, idea, "host", "agenda", session.topic_id or "T1")]
        item = session.current_item()
        topic = f"{item.title}｜{item.question}" if item else None
        draft = session.current_draft
        if session.status == SessionStatus.REVIEWING and session.waiting_for == WaitingFor.REVIEW and draft:
            parent_id = draft.parent_id
            if parent_id is None and draft.version > 1:
                # 兼容已冻结的旧会话，只读取本会话紧邻的上一版本，无需回写历史。
                parent_id = next((d.draft_id for d in session.drafts if d.version == draft.version - 1), None)
            return [self._job(session, history, idea, "reviewer", "review", session.topic_id or "T1",
                              topic=topic, draft=draft, issues=self._pending_review_issues(session, parent_id, history))]
        if session.status != SessionStatus.DISCUSSING or session.waiting_for != WaitingFor.ROLE_RESULTS:
            return []
        jobs = []
        for task in session.tasks.values():
            all_messages = task.kind in (TaskKind.INTEGRATION, TaskKind.DRAFT, TaskKind.SCORING)
            messages = history if all_messages else [m for m in history if m.seq in task.message_seqs]
            previous = session.drafts[-1] if session.drafts and all_messages else None
            issues = self._review_issues(session, previous.draft_id, history) if previous else []
            if task.kind in (TaskKind.INTEGRATION, TaskKind.DRAFT):
                known = {issue.issue_id for issue in issues}
                issues.extend(
                    self._prior_issue(session, issue, issue.assignee.value)
                    for issue in session.issues.values()
                    if issue.raised_by == Role.USER and issue.input_version == session.input_version
                    and issue.assignee and issue.response and issue.issue_id not in known
                )
            parent = draft if all_messages else None
            # 回答被替换后只能使用新的可见消息重建正文；旧评审项仍要逐项复核。
            if previous and issues and (not replaced or previous.input_version == session.input_version):
                parent = previous
            if task.kind == TaskKind.SCORING:
                # Older snapshots did not store the tradeoff IDs on scoring tasks.
                issue_ids = task.issue_ids or tuple(
                    issue.issue_id for issue in session.open_issues(task.topic_id)
                    if issue.kind == DivergenceKind.TRADEOFF
                )
                issues = [self._prior_issue(session, session.issues[issue_id], "recorder") for issue_id in issue_ids
                          if issue_id in session.issues]
                parent = None
            elif task.kind == TaskKind.RESPONSE:
                issues = [self._prior_issue(session, session.issues[issue_id], task.role.value) for issue_id in task.issue_ids
                          if issue_id in session.issues]
            previous_ids = ()
            if task.kind == TaskKind.DRAFT and previous and parent is None:
                previous_ids = tuple(item.requirement_id for item in DraftSpec.parse(previous.content).requirements)
            card = next((card for card in reversed(session.scorecards)
                         if card.input_version == session.input_version and card.topic_id == task.topic_id), None)
            candidates = () if task.kind != TaskKind.STANCE or card is None else card.options
            jobs.append(self._job(session, messages, idea, task.role.value, task.kind.value, task.topic_id,
                                  task=task, topic=topic, allowed_roles=item.role_scope if item else None,
                                  draft=parent, issues=issues, candidates=candidates, previous_ids=previous_ids))
        return jobs

    @staticmethod
    def _prior_issue(session, issue, owner) -> PriorIssue:
        response_seq = issue.response_seq or issue.closed_by_seq
        closing = next((m.text for m in session.messages if m.seq == response_seq), None)
        refs = list(issue.source_seqs)
        if issue.response:
            refs.extend(issue.response.source_seqs)
        if closing:
            refs.append(response_seq)
        return PriorIssue(issue_id=issue.issue_id, severity="blocking" if issue.severity.value == "blocking" else "advisory",
                          kind=getattr(getattr(issue, "kind", None), "value", "fact"),
                          target_item="background", description=issue.summary,
                          owner_role=ROLE_ALIASES.get(owner, owner),
                          closure_condition=issue.summary,
                          treatment=issue.response.treatment if issue.response else closing,
                          source_seqs=list(dict.fromkeys(refs)))

    def _pending_review_issues(self, session, parent_id, history) -> list[PriorIssue]:
        issues = self._review_issues(session, parent_id, history)
        known = {issue.issue_id for issue in issues}
        for issue in session.issues.values():
            if (issue.issue_id not in known and issue.input_version == session.input_version
                    and issue.status.value == "answered" and issue.response and issue.severity.value == "blocking"):
                issues.append(self._prior_issue(session, issue, issue.assignee.value if issue.assignee else "recorder"))
        return issues

    def _review_issues(self, session, draft_id, history) -> list[PriorIssue]:
        review = session.review_of(draft_id) if draft_id else None
        if review is None:
            return []
        visible = {m.seq: m for m in history if m.text.strip()}
        result = []
        for finding in review.findings:
            # NEEDS_INPUT 的发现存在评审记录中，只有 REVISE 会创建定向整改任务。
            key = finding.issue_id or finding.finding_id
            issue = session.issues.get(key)
            closure = (issue.response_seq or issue.closed_by_seq) if issue and issue.input_version == review.input_version else None
            message = visible.get(closure)
            owner = finding.responsible_role.value if finding.responsible_role else "recorder"
            refs = list(finding.source_seqs)
            response = issue.response if issue and closure else None
            if response:
                refs.extend(seq for seq in response.source_seqs if seq not in refs)
            if message and message.seq not in refs:
                refs.append(message.seq)
            result.append(PriorIssue(issue_id=key, target_item=finding.target_item, description=finding.summary,
                                     severity="blocking" if finding.severity.value == "blocking" else "advisory",
                                     owner_role=ROLE_ALIASES.get(owner, owner), closure_condition=finding.close_condition,
                                     treatment=response.treatment if response else message.text if message else None, source_seqs=refs))
        return result

    @staticmethod
    def _job(session, messages, idea, role, kind, topic_id, *, task=None, topic=None, allowed_roles=None, draft=None, issues=(), candidates=(), previous_ids=()) -> Job:
        task_id = (task.task_id if task else f"{draft.draft_id}:agent:review" if kind == "review"
                   else f"{session.session_id}:agent:agenda:v{session.input_version}")
        snapshot = None
        if draft:
            spec = DraftSpec.parse(draft.content)
            snapshot = DraftSnapshot(id=draft.draft_id, session_id=session.session_id, input_version=draft.input_version,
                                     revision=draft.version, content=spec.to_content(),
                                     source_map={req.requirement_id: list(req.source_seqs) for req in spec.requirements},
                                     analysis=AgentExecution._message_analysis(draft))
        messages = [m for m in messages if m.text.strip()]
        if kind in {"integration", "draft", "review", "scoring"}:
            # Keep the evidence needed to validate the frozen draft and prior
            # findings. Unrelated early discussion does not need to be resent.
            cited = {seq for issue in issues for seq in issue.source_seqs}
            if snapshot:
                cited.update(seq for seqs in snapshot.source_map.values() for seq in seqs)
            keep = set(cited)
            keep.update(message.seq for message in messages[-24:] if message.kind == MessageKind.STANCE)
            remaining = 16000
            for message in reversed(messages[-24:]):
                if message.seq not in keep and message.role != Role.USER and len(message.text) <= remaining:
                    keep.add(message.seq)
                    remaining -= len(message.text)
            messages = [
                message for message in messages if message.seq in keep or message.role == Role.USER
            ]
        visible = {m.seq for m in messages}
        request = RoleRequest(
            role=ROLE_ALIASES.get(role, role), kind=kind,
            logical_task_id=task_id, session_id=session.session_id,
            input_version=session.input_version, topic_id=topic_id, topic=topic, user_input=idea,
            round=max(1, session.round_no),
            previous_requirement_ids=list(previous_ids),
            candidate_options=[CandidateOption(option_id=option.option_id, title=option.title) for option in candidates],
            consensus=list(dict.fromkeys(agreement for message in messages for agreement in message.agreements
                                         if len(agreement) <= 600))[-12:],
            messages=[
                SourceMessage(session_id=session.session_id, input_version=m.input_version, seq=m.seq,
                              role=ROLE_ALIASES.get(m.role.value, m.role.value), text=m.text,
                              analysis=AgentExecution._message_analysis(m))
                for m in messages
            ], draft=snapshot,
            issues=[issue.model_copy(update={"source_seqs": [seq for seq in issue.source_seqs if seq in visible]}) for issue in issues],
            **({"allowed_roles": [ROLE_ALIASES.get(r.value, r.value) for r in allowed_roles]}
               if allowed_roles is not None else {}),
        )
        return Job(request, role, kind, session.snapshot_ref, task)

    @staticmethod
    def _message_analysis(message) -> dict | None:
        notes = {key: value for key in ("basis", "verification")
                 if (value := getattr(message, key, None)) and len(value) <= 600}
        for key in ("agreements", "alternatives", "disagreements", "assumptions", "risks"):
            items = [item for item in getattr(message, key) if len(item) <= 600][:3]
            if items:
                notes[key] = items
        return notes or None

    @staticmethod
    def _apply(service, job: Job, output: dict, owner: str) -> bool:
        request = job.request
        task = job.task
        if job.kind == "review":
            findings = {"open": [], "closed": []}
            for finding in output["findings"]:
                role = finding["owner_role"]
                findings[finding["status"]].append({
                    "finding_id": finding["issue_id"], "issue_id": finding["issue_id"],
                    "source_seqs": finding["source_seqs"],
                    "severity": "blocking" if finding["severity"] == "blocking" else "major",
                    "target_item": finding["target_item"], "summary": finding["description"],
                    "close_condition": finding["closure_condition"],
                    "responsible_role": "product" if role == "scribe" else ENGINE_ROLES.get(role, role),
                })
            payload = {"review_id": request.logical_task_id, "session_id": request.session_id,
                       "draft_id": output["draft_id"], "input_version": request.input_version,
                       "verdict": output["verdict"].lower(), "questions": output["questions"],
                       "question_options": output.get("question_options", {}),
                       "findings": findings["open"], "verified_findings": findings["closed"]}
            action = "submit_review"
        elif task is None:
            payload = {"summary": output["text"], "items": [{"topic_id": request.topic_id,
                       "title": request.user_input.splitlines()[0][:80],
                       "question": "首版应该解决哪些用户需求与关键约束？"}],
                       "missing_facts": output["questions"], "question_options": output.get("question_options", {})}
            action = "submit_agenda"
        else:
            payload = {"task_id": task.task_id, "session_id": task.session_id, "role": job.role, "kind": job.kind,
                       "input_version": task.input_version, "snapshot_ref": task.snapshot_ref,
                       "round_no": task.round_no, "topic_id": task.topic_id, "text": output["text"],
                       "source_seqs": output["source_seqs"]}
            for key in ("basis", "verification", "agreements", "alternatives", "disagreements", "assumptions", "risks"):
                if key in output:
                    payload[key] = output[key]
            if task.kind == TaskKind.CHALLENGE:
                payload["raised_issues"] = [{"issue_id": f"I-{task.round_no}-{task.task_id.rsplit(':', 1)[-1]}",
                                             "topic_id": task.topic_id, "raised_by": job.role, "severity": "major",
                                             "kind": output.get("divergence", "fact"),
                                             "user_question": output.get("user_question"),
                                             "summary": f"{output['text']} {output['verification']}",
                                             "source_seqs": output["source_seqs"]}]
            elif task.kind == TaskKind.RESPONSE:
                responses = output["responses"]
                payload["issue_ids"] = [response["issue_id"] for response in responses]
                payload["responses"] = responses
                payload["source_seqs"] = list(dict.fromkeys([
                    *payload["source_seqs"], *(seq for item in output["responses"] for seq in item["source_seqs"]),
                ]))
                payload["text"] += "\n" + "\n".join(
                    f"{response['issue_id']}：{response['treatment']}。验证方法：{response['verification']}"
                    for response in responses
                )
            elif task.kind == TaskKind.STANCE:
                positions = {"support": "赞同", "oppose": "反对", "conditional": "有条件赞同"}
                payload["stances"] = output["stances"]
                payload["source_seqs"] = list(dict.fromkeys([
                    *payload["source_seqs"], *(seq for item in output["stances"] for seq in item["source_seqs"]),
                ]))
                payload["item_refs"] = [stance["option_id"] for stance in output["stances"]]
                payload["text"] += "\n" + "\n".join(
                    f"{stance['option_id']} · {positions[stance['position']]}：{stance['reason']}"
                    for stance in output["stances"]
                )
            elif task.kind == TaskKind.SCORING:
                scorecard = output.get("scorecard") or {}
                payload["scorecard"] = {
                    "options": scorecard.get("options", []),
                    "rationale": scorecard.get("rationale", ""),
                }
                payload["item_refs"] = [
                    str(option.get("option_id", "")) for option in payload["scorecard"]["options"]
                ]
            elif task.kind == TaskKind.DRAFT:
                content = output["content"]
                payload["draft_content"] = {**content, "requirements": [
                    {**item, "source_seqs": output["source_map"][item["id"]]} for item in content["requirements"]
                ], "removed_requirement_ids": [change["previous_id"] for change in output["requirement_changes"]
                                                if change["previous_id"] not in output["source_map"]]}
                payload["item_refs"] = list(output["source_map"])
                # 分歧记录随草稿版本落库，导出 DesignSpecContent 时不会带出。
                payload["agreements"] = output.get("agreements", [])
                payload["alternatives"] = output.get("alternatives", [])
                payload["disagreements"] = output.get("disagreements", [])
            action = "submit_output"

        def is_current(session):
            if session.input_version != request.input_version:
                return False
            if job.kind == "review":
                return (session.status == SessionStatus.REVIEWING and session.waiting_for == WaitingFor.REVIEW
                        and session.current_draft is not None and session.current_draft.draft_id == output["draft_id"])
            if task is None:
                return session.status == SessionStatus.CLARIFYING and session.waiting_for == WaitingFor.HOST_AGENDA
            return session.status == SessionStatus.DISCUSSING and session.tasks.get(task.task_id) == task

        return AgentExecution._commit(service, request.session_id, action, payload, is_current, owner=owner)

    @staticmethod
    def _commit(service, session_id, action, payload, is_current, *, owner, allow_rejection=False) -> bool:
        for _ in range(3):
            session, meta = service._load(session_id)
            if not is_current(session):
                return False
            try:
                _, result = service.act(session_id, action=action, payload=payload,
                                        expected_state_version=meta["state_version"], idempotency_key=None,
                                        request_hash="", scope="", lease_owner=owner)
                if not result["decision"]["ok"]:
                    if allow_rejection:
                        return False
                    raise ApiError(result["decision"]["code"], result["decision"]["message"], status=409)
                return True
            except VersionConflict:
                continue
            except ApiError as exc:
                if exc.code != "REVISION_CONFLICT":
                    if allow_rejection:
                        return False
                    raise
        raise ApiError("REVISION_CONFLICT", "讨论状态已更新", status=409)

    def _start(self, service, job: Job, owner: str) -> tuple[int, str | None]:
        previous = service.repo.latest_attempt(job.request.session_id, job.request.logical_task_id)
        number = previous["attempt_no"] + 1 if previous else 1
        diagnostic = None
        if (previous and previous["input_version"] == job.request.input_version
                and previous["snapshot_ref"] == job.snapshot_ref):
            error = previous["error"] or {}
            if error.get("code") == "invalid_output":
                diagnostic = error.get("diagnostic")
        self._record(service, job, owner, number)
        return number, diagnostic

    @staticmethod
    def _record(service, job: Job, owner: str, number: int) -> None:
        service.repo.record_attempt(session_id=job.request.session_id, logical_task_id=job.request.logical_task_id,
                                    attempt_no=number, role=job.role, task_kind=job.kind,
                                    snapshot_ref=job.snapshot_ref, input_version=job.request.input_version, status="RUNNING", lease_owner=owner)

    @staticmethod
    def _finish(service, job: Job, owner: str, number: int, status: str, **fields) -> None:
        service.repo.finish_attempt(session_id=job.request.session_id, logical_task_id=job.request.logical_task_id,
                                    attempt_no=number, status=status, lease_owner=owner, **fields)
