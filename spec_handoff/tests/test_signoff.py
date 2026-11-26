"""签收门禁与交接记录。"""

from __future__ import annotations

import unittest

from discussion_orchestration import SessionStatus, Verdict

from spec_handoff import Code, content_hash, export_handoff, export_handoff_bundle, sign_off
from spec_handoff.tests.support import blocking_finding, blocking_issue, make_draft, make_session, with_requirement


def _codes(result) -> set[str]:
    return {finding.code.value for finding in result.findings}


class SignOffGateTest(unittest.TestCase):
    def test_passed_review_produces_handoff(self) -> None:
        session = make_session()

        result = sign_off(session, draft_id="s-1-v1", input_version=1, now="2026-11-23T08:00:00+00:00")

        self.assertTrue(result.ok, result.findings)
        handoff = result.handoff
        assert handoff is not None
        self.assertEqual(handoff.session_id, "s-1")
        self.assertEqual(handoff.project_id, "p-1")
        self.assertEqual(handoff.draft_version, 1)
        self.assertEqual(handoff.review_id, "rv-1")
        self.assertEqual(handoff.confirmed_at, "2026-11-23T08:00:00+00:00")
        self.assertEqual(handoff.content_hash, content_hash(handoff.content))
        self.assertIn("FR-02", handoff.content["requirements"][1]["id"])

    def test_revise_verdict_can_be_signed_while_preserving_review_outcome(self) -> None:
        session = make_session(verdict=Verdict.REVISE, findings=(blocking_finding(),))
        session.current_draft.review_id = "rv-1"
        result = sign_off(session, draft_id="s-1-v1", input_version=1)
        self.assertTrue(result.ok, result.findings)
        self.assertEqual(result.handoff.review_verdict, "revise")
        self.assertEqual(result.handoff.review_id, "rv-1")
        self.assertEqual(session.review_of("s-1-v1").verdict, Verdict.REVISE)

    def test_blocking_finding_inside_pass_review_blocks(self) -> None:
        session = make_session(findings=(blocking_finding(),))

        result = sign_off(session, draft_id="s-1-v1", input_version=1)

        self.assertFalse(result.ok)
        self.assertIn(Code.BLOCKING_FINDING.value, _codes(result))

    def test_open_blocking_issue_blocks(self) -> None:
        session = make_session(issues={"I-1": blocking_issue()})

        result = sign_off(session, draft_id="s-1-v1", input_version=1)

        self.assertFalse(result.ok)
        self.assertIn(Code.OPEN_BLOCKING_ISSUE.value, _codes(result))

    def test_stale_draft_blocks(self) -> None:
        session = make_session()

        result = sign_off(session, draft_id="s-1-v0", input_version=1)

        self.assertFalse(result.ok)
        self.assertIn(Code.STALE_DRAFT.value, _codes(result))

    def test_stale_input_version_blocks(self) -> None:
        session = make_session()

        result = sign_off(session, draft_id="s-1-v1", input_version=2)

        self.assertFalse(result.ok)
        self.assertIn(Code.STALE_INPUT_VERSION.value, _codes(result))

    def test_status_must_await_confirmation(self) -> None:
        session = make_session(status=SessionStatus.DISCUSSING)

        result = sign_off(session, draft_id="s-1-v1", input_version=1)

        self.assertFalse(result.ok)
        self.assertIn(Code.INVALID_STATUS.value, _codes(result))

    def test_invalid_draft_content_blocks_sign_off(self) -> None:
        session = make_session(with_requirement(0, source_seqs=[]))

        result = sign_off(session, draft_id="s-1-v1", input_version=1)

        self.assertFalse(result.ok)
        self.assertIn(Code.SOURCE_MISSING.value, _codes(result))

    def test_signoff_rejects_structurally_valid_content_that_is_too_large_to_import(self) -> None:
        draft = make_draft()
        template = draft["requirements"][0]
        draft["requirements"] = [
            {**template, "id": f"FR-{index:03d}", "acceptance_criteria": [f"场景 {item}：" + "中" * 1900 for item in range(20)]}
            for index in range(1, 12)
        ]
        session = make_session(draft)
        result = sign_off(session, draft_id="s-1-v1", input_version=1)
        self.assertFalse(result.ok)
        self.assertIn(Code.EXPORT_TOO_LARGE.value, _codes(result))

    def test_parent_draft_id_stability_enforced(self) -> None:
        parent = make_draft()
        parent["requirements"].append(
            {
                "id": "FR-09",
                "kind": "functional",
                "priority": "should",
                "text": "行政可临时停用会议室。",
                "acceptance_criteria": ["停用后立即阻止新预约"],
                "source_seqs": [2],
                "covers": [],
            }
        )
        session = make_session(make_draft(), parent=parent)

        result = sign_off(session, draft_id="s-1-v1", input_version=1)

        self.assertFalse(result.ok)
        self.assertIn(Code.ID_DROPPED.value, _codes(result))


class HandoffExportTest(unittest.TestCase):
    def _handoff(self):
        session = make_session()
        result = sign_off(session, draft_id="s-1-v1", input_version=1, now="2026-11-23T08:00:00+00:00")
        assert result.handoff is not None
        return result.handoff

    def test_plain_export_is_spec_content_only(self) -> None:
        import json

        text = export_handoff(self._handoff())
        payload = json.loads(text)

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertNotIn("handoff", payload)
        self.assertTrue(text.endswith("\n"))

    def test_bundle_keeps_trace_metadata(self) -> None:
        import json

        payload = json.loads(export_handoff_bundle(self._handoff()))

        self.assertEqual(payload["handoff"]["draft_id"], "s-1-v1")
        self.assertEqual(payload["spec_content"]["product_name"], "内部会议室预约")


if __name__ == "__main__":
    unittest.main()
