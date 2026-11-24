"""Drive discussion roles until the engine needs a user decision."""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from discussion_orchestration import MessageKind, Role, SessionStatus, TaskKind, WaitingFor
from spec_handoff.draft import DraftSpec

from backend.roles.contracts import DraftSnapshot, PriorIssue, RoleRequest, SourceMessage
from backend.roles.provider import DeepSeekProvider
from backend.roles.runner import OUTPUT_CORRECTION_ROLES, RoleRunner
from backend.roles.simulation import SimulationProvider
from .errors import ApiError
from .repository import VersionConflict

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
    def __init__(self) -> None:
        self.mode = os.environ.get("DISCUSSION_AGENT_MODE", "simulation").strip().lower()
        if self.mode not in ("simulation", "real", "off"):
            raise ValueError("DISCUSSION_AGENT_MODE must be simulation, real, or off")
        self.pending: dict[str, bool] = {}

    async def run(self, service: "DiscussionService", session_id: str) -> None:
        if self.mode == "off":
            return
        if session_id in self.pending:
            self.pending[session_id] = True
            return
        self.pending[session_id] = False
        try:
            while True:
                self.pending[session_id] = False
                await self._drive(service, session_id)
                if not self.pending[session_id]:
                    return
        except Exception:
            logger.exception("Automatic role execution failed for discussion %s", session_id)
        finally:
            self.pending.pop(session_id, None)

    async def _drive(self, service: "DiscussionService", session_id: str) -> None:
        session, _ = service._load(session_id)
        jobs = self._next_jobs(service, session)
        if not jobs:
            return
        try:
            provider = (DeepSeekProvider(api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
                                         model=os.environ.get("DISCUSSION_MODEL", "deepseek-flash").strip(), max_tokens=8192)
                        if self.mode == "real" else SimulationProvider())
        except ValueError:
            for job in jobs:
                number, _ = self._start(service, job)
                self._finish(service, job, number, "FAILED", error={"code": "PROVIDER_CONFIG"})
            return

        runner = RoleRunner(session_id, provider, mode=self.mode)
        while jobs:
            starts = [self._start(service, job) for job in jobs]
            results = await asyncio.gather(*(runner.run(job.request, validation_diagnostic=diagnostic)
                                             for job, (_, diagnostic) in zip(jobs, starts)), return_exceptions=True)
            failed = False
            for job, (number, _), result in zip(jobs, starts, results):
                if isinstance(result, BaseException):
                    self._finish(service, job, number, "FAILED", error={"code": "AGENT_EXECUTION_FAILED"})
                    failed = True
                    continue
                applied = False
                error = result.error
                if result.output is not None:
                    try:
                        applied = self._apply(service, job, result.output)
                    except (ApiError, VersionConflict) as exc:
                        error = getattr(exc, "code", "REVISION_CONFLICT")
                for index, attempt in enumerate(result.attempts):
                    if index:
                        self._record(service, job, number + index)
                    last = index == len(result.attempts) - 1
                    code = error if last else attempt.error
                    status = "FAILED" if code else "SUCCEEDED" if applied else "IGNORED"
                    failure = {"code": code} if code else None
                    if code == "invalid_output" and attempt.diagnostic:
                        failure["diagnostic"] = attempt.diagnostic
                    self._finish(service, job, number + index, status, usage=attempt.usage,
                                 error=failure)
                if not result.attempts:
                    self._finish(service, job, number, "FAILED", error={"code": error or "NO_OUTPUT"})
                failed |= bool(error)
            if failed:
                return
            session, _ = service._load(session_id)
            if session.input_version != jobs[0].request.input_version:
                return  # A substantive message schedules execution for the new input.
            jobs = self._next_jobs(service, session)

    def _next_jobs(self, service, session) -> list[Job]:
        if (session.status == SessionStatus.DISCUSSING and session.waiting_for == WaitingFor.HOST_DECISION
                and not session.tasks and session.current_item() is not None):
            item = session.current_item()
            issues = session.open_issues(item.topic_id)
            if issues and (any(issue.needs_user for issue in issues)
                           or session.naming_passes >= session.limits.max_naming_passes_per_round):
                proposal = {"action": "ask_user", "questions": [issue.summary for issue in issues],
                            "issue_ids": [issue.issue_id for issue in issues]}
            elif issues:
                assignments = {}
                sources = {message.seq: message.role for message in session.messages}
                for issue in issues:
                    role = issue.assignee or next((sources.get(seq) for seq in reversed(issue.source_seqs)
                                                   if sources.get(seq) in item.role_scope), item.role_scope[0])
                    assignments.setdefault(role.value, []).append(issue.issue_id)
                proposal = {"action": "name_responses", "assignments": assignments}
            else:
                items = session.agenda.items
                proposal = {"action": "submit_draft" if item == items[-1] else "next_topic"}
            if self._commit(service, session.session_id, "propose", proposal,
                            lambda current: current.status == session.status and current.waiting_for == session.waiting_for
                            and current.last_seq == session.last_seq and current.input_version == session.input_version,
                            allow_rejection=True):
                session, _ = service._load(session.session_id)
        return self._jobs(session)

    def _jobs(self, session) -> list[Job]:
        replaced = {m.replaces_answer_seq for m in session.messages if m.replaces_answer_seq is not None}
        history = [m for m in session.messages if m.seq not in replaced
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
            return [self._job(session, history, idea, "reviewer", "review", session.topic_id or "T1",
                              topic=topic, draft=draft, issues=self._review_issues(session, draft.parent_id))]
        if session.status != SessionStatus.DISCUSSING or session.waiting_for != WaitingFor.ROLE_RESULTS:
            return []
        jobs = []
        for task in session.tasks.values():
            all_messages = task.kind in (TaskKind.INTEGRATION, TaskKind.DRAFT)
            messages = history if all_messages else [m for m in history if m.seq in task.message_seqs]
            issues = self._review_issues(session, draft.draft_id) if all_messages and draft else []
            if task.kind == TaskKind.RESPONSE:
                issues = [self._prior_issue(session, session.issues[issue_id], task.role.value) for issue_id in task.issue_ids
                          if issue_id in session.issues]
            jobs.append(self._job(session, messages, idea, task.role.value, task.kind.value, task.topic_id,
                                  task=task, topic=topic, allowed_roles=item.role_scope if item else None,
                                  draft=draft if all_messages else None, issues=issues))
        return jobs

    @staticmethod
    def _prior_issue(session, issue, owner, finding=None) -> PriorIssue:
        closing = next((m.text for m in session.messages if m.seq == issue.closed_by_seq), None)
        return PriorIssue(issue_id=issue.issue_id, severity="blocking" if issue.severity.value == "blocking" else "advisory",
                          target_item=finding.target_item if finding else "background", description=issue.summary,
                          owner_role=ROLE_ALIASES.get(owner, owner),
                          closure_condition=finding.close_condition if finding else issue.summary,
                          treatment=closing, source_seqs=[issue.closed_by_seq] if closing else [])

    def _review_issues(self, session, draft_id) -> list[PriorIssue]:
        review = session.review_of(draft_id) if draft_id else None
        return [self._prior_issue(session, session.issues[finding.issue_id], finding.responsible_role.value, finding)
                for finding in (review.findings if review else ())
                if finding.issue_id in session.issues and finding.responsible_role is not None]

    @staticmethod
    def _job(session, messages, idea, role, kind, topic_id, *, task=None, topic=None, allowed_roles=None, draft=None, issues=()) -> Job:
        task_id = (task.task_id if task else f"{draft.draft_id}:agent:review" if kind == "review"
                   else f"{session.session_id}:agent:agenda:v{session.input_version}")
        snapshot = None
        if draft:
            spec = DraftSpec.parse(draft.content)
            snapshot = DraftSnapshot(id=draft.draft_id, session_id=session.session_id, input_version=draft.input_version,
                                     revision=draft.version, content=spec.to_content(),
                                     source_map={req.requirement_id: list(req.source_seqs) for req in spec.requirements})
        visible = {m.seq for m in messages}
        request = RoleRequest(
            role=ROLE_ALIASES.get(role, role), logical_task_id=task_id, session_id=session.session_id,
            input_version=session.input_version, topic_id=topic_id, topic=topic, user_input=idea,
            round=max(1, session.round_no), messages=[
                SourceMessage(session_id=session.session_id, input_version=m.input_version, seq=m.seq,
                              role=ROLE_ALIASES.get(m.role.value, m.role.value), text=m.text)
                for m in messages if m.text.strip()
            ], draft=snapshot,
            issues=[issue.model_copy(update={"source_seqs": [seq for seq in issue.source_seqs if seq in visible]}) for issue in issues],
            **({"allowed_roles": [ROLE_ALIASES.get(r.value, r.value) for r in allowed_roles]}
               if allowed_roles is not None else {}),
        )
        return Job(request, role, kind, session.snapshot_ref, task)

    @staticmethod
    def _apply(service, job: Job, output: dict) -> bool:
        request = job.request
        task = job.task
        if job.kind == "review":
            payload = {"review_id": request.logical_task_id, "session_id": request.session_id,
                       "draft_id": output["draft_id"], "input_version": request.input_version,
                       "verdict": output["verdict"].lower(), "questions": output["questions"],
                       "question_options": output.get("question_options", {}), "findings": [
                           {"finding_id": finding["issue_id"], "issue_id": finding["issue_id"],
                            "severity": "blocking" if finding["severity"] == "blocking" else "major",
                            "target_item": finding["target_item"], "summary": finding["description"],
                            "close_condition": finding["closure_condition"],
                            "responsible_role": ENGINE_ROLES.get(finding["owner_role"], finding["owner_role"])
                            if finding["owner_role"] != "scribe" else "product"}
                           for finding in output["findings"] if finding["status"] == "open"
                       ]}
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
            if task.kind == TaskKind.CHALLENGE:
                payload["raised_issues"] = [{"issue_id": f"I-{task.round_no}-{task.task_id.rsplit(':', 1)[-1]}",
                                             "topic_id": task.topic_id, "raised_by": job.role, "severity": "major",
                                             "summary": f"{output['text']} {output['verification']}",
                                             "source_seqs": output["source_seqs"]}]
            elif task.kind == TaskKind.RESPONSE:
                payload["issue_ids"] = list(task.issue_ids)
                if verification := output.get("verification"):
                    payload["text"] = f"{payload['text']}\n验证方法：{verification}"
                    payload["evidence"] = [verification]
            elif task.kind == TaskKind.DRAFT:
                content = output["content"]
                payload["draft_content"] = {**content, "requirements": [
                    {**item, "source_seqs": output["source_map"][item["id"]]} for item in content["requirements"]
                ], "removed_requirement_ids": [change["previous_id"] for change in output["requirement_changes"]
                                                if change["previous_id"] not in output["source_map"]]}
                payload["item_refs"] = list(output["source_map"])
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

        return AgentExecution._commit(service, request.session_id, action, payload, is_current)

    @staticmethod
    def _commit(service, session_id, action, payload, is_current, *, allow_rejection=False) -> bool:
        for _ in range(3):
            session, meta = service._load(session_id)
            if not is_current(session):
                return False
            try:
                _, result = service.act(session_id, action=action, payload=payload,
                                        expected_state_version=meta["state_version"], idempotency_key=None,
                                        request_hash="", scope="")
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

    def _start(self, service, job: Job) -> tuple[int, str | None]:
        existing = (row for row in service.repo.list_attempts(job.request.session_id)
                    if row["logical_task_id"] == job.request.logical_task_id)
        previous = max(existing, key=lambda row: row["attempt_no"], default=None)
        number = previous["attempt_no"] + 1 if previous else 1
        diagnostic = None
        if (previous and job.request.role in OUTPUT_CORRECTION_ROLES
                and previous["input_version"] == job.request.input_version
                and previous["snapshot_ref"] == job.snapshot_ref):
            error = previous["error"] or {}
            if error.get("code") == "invalid_output":
                diagnostic = error.get("diagnostic")
        self._record(service, job, number)
        return number, diagnostic

    @staticmethod
    def _record(service, job: Job, number: int) -> None:
        service.repo.record_attempt(session_id=job.request.session_id, logical_task_id=job.request.logical_task_id,
                                    attempt_no=number, role=job.role, task_kind=job.kind,
                                    snapshot_ref=job.snapshot_ref, input_version=job.request.input_version, status="RUNNING")

    @staticmethod
    def _finish(service, job: Job, number: int, status: str, **fields) -> None:
        service.repo.finish_attempt(session_id=job.request.session_id, logical_task_id=job.request.logical_task_id,
                                    attempt_no=number, status=status, **fields)
