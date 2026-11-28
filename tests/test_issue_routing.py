from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from backend.discussions.agent_execution import AgentExecution
from backend.discussions.codec import decode
from backend.discussions.routing import fact_routes, stalled_facts
from discussion_orchestration import (
    Agenda, AgendaItem, DiscussionEngine, HostProposal, Issue, IssueStatus, MessageKind,
    Phase, Role, SessionStatus, Severity, WaitingFor,
)
from discussion_orchestration.models import IssueResponse


class IssueRoutingTest(unittest.TestCase):
    def setUp(self):
        self.engine = DiscussionEngine()
        self.session = self.engine.create_session(session_id="routing", project_id="project", idea="内部预约")
        self.engine.submit_agenda(self.session, Agenda("预约", (AgendaItem("T1", "预约", "如何检查冲突？"),)))
        self.session.tasks.clear()
        self.session.phase = Phase.CHALLENGE
        self.session.round_challenged = True
        self.session.waiting_for = WaitingFor.HOST_DECISION
        self.issue = Issue("current", "T1", Role.CHALLENGER, "预约的并发事务如何避免重复？",
                           Severity.MAJOR, source_seqs=(1,), closure_condition="验证并发请求只写入一条记录")

    def test_shadow_compares_without_replacing_baseline_and_active_preserves_owner(self):
        item = self.session.current_item()
        source = self.engine._append(self.session, Role.PRODUCT, MessageKind.STATEMENT, "需要避免重复")
        issue = replace(self.issue, source_seqs=(source.seq,))
        shadow, comparison = fact_routes([issue], item.role_scope, self.session.messages, active=False)
        self.assertEqual(shadow, {"product": ["current"]})
        self.assertEqual(comparison[0]["suggested"], "architect")
        self.assertEqual(comparison[0]["applied_reason"], "source_author")
        active, _ = fact_routes([issue], item.role_scope, self.session.messages, active=True)
        self.assertEqual(active, {"architect": ["current"]})
        explicit, _ = fact_routes([replace(issue, assignee=Role.DESIGNER)], item.role_scope, self.session.messages, active=True)
        self.assertEqual(explicit, {"designer": ["current"]})

    def _add_stalled_history(self):
        for identifier in ("first", "second"):
            message = self.engine._append(self.session, Role.ARCHITECT, MessageKind.RESPONSE, "仍需确认落库行为")
            self.session.issues[identifier] = replace(
                self.issue, issue_id=identifier, status=IssueStatus.ANSWERED, response_seq=message.seq,
                response=IssueResponse(identifier, message.text, "用并发请求验证", (1,)),
            )

    def test_new_evidence_or_changed_response_avoids_stall(self):
        self._add_stalled_history()
        self.assertEqual(stalled_facts(self.session, [self.issue]), [self.issue])
        evidence = self.engine._append(self.session, Role.USER, MessageKind.USER_INPUT, "已有事务隔离测试结果")
        self.assertEqual(stalled_facts(self.session, [replace(self.issue, source_seqs=(evidence.seq,))]), [])
        second = self.session.issues["second"]
        self.session.issues["second"] = replace(second, response=replace(second.response, treatment="已找到冲突测试"))
        self.assertEqual(stalled_facts(self.session, [self.issue]), [])

    def test_stall_defers_advisory_but_clarifies_blocker(self):
        for severity in (Severity.MAJOR, Severity.BLOCKING):
            with self.subTest(severity=severity):
                self.setUp()
                self._add_stalled_history()
                issue = replace(self.issue, severity=severity)
                self.session.issues[issue.issue_id] = issue
                service = Mock()
                service._load.return_value = (self.session, {})
                execution = AgentExecution()

                def commit(service, sid, action, payload, current, **kwargs):
                    self.assertTrue(current(self.session))
                    return self.engine.propose(self.session, decode(HostProposal, payload)).ok

                with patch.object(execution, "_commit", side_effect=commit), patch.object(execution, "_jobs", return_value=[]):
                    execution._next_jobs(service, self.session, "worker")
                if severity == Severity.BLOCKING:
                    self.assertEqual(self.session.status, SessionStatus.CLARIFYING)
                    self.assertTrue(issue.needs_user)
                else:
                    self.assertEqual(self.session.phase, Phase.INTEGRATION)
                    self.assertEqual(issue.status, IssueStatus.DEFERRED)
                    self.assertIsNotNone(issue.closed_by_seq)
