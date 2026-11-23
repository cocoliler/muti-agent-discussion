"""草稿转换与交付校验。"""

from __future__ import annotations

import copy
import unittest

from spec_handoff import Code, DraftSpec, build_report, check_content, parent_draft
from spec_handoff.tests.support import make_draft, make_session, with_requirement


def _codes(report) -> set[str]:
    return {finding.code.value for finding in report.findings}


class DraftConversionTest(unittest.TestCase):
    def test_draft_becomes_design_spec_content(self) -> None:
        report = build_report(DraftSpec.parse(make_draft()), known_seqs=(1, 2))

        self.assertTrue(report.ok, report.findings)
        content = report.content
        self.assertEqual(
            list(content),
            [
                "schema_version",
                "product_name",
                "background",
                "goals",
                "target_users",
                "requirements",
                "technical_constraints",
                "development_constraints",
                "delivery_requirements",
                "acceptance_criteria",
                "supplemental_text",
                "confirmed_assumptions",
            ],
        )
        self.assertEqual(content["schema_version"], "1.0")
        self.assertEqual(
            content["requirements"][0],
            {
                "id": "FR-01",
                "kind": "functional",
                "priority": "must",
                "text": "员工可按时间与容量筛选可用会议室并创建预约。",
                "acceptance_criteria": ["选择可用时段后返回确认记录"],
            },
        )

    def test_traceability_fields_never_leak_into_content(self) -> None:
        content = DraftSpec.parse(make_draft()).to_content()

        text = str(content)
        for leaked in ("source_seqs", "covers", "pain_points", "risks", "removed_requirement_ids"):
            self.assertNotIn(leaked, text)
        codes = {finding.code for finding in check_content({**content, "risks": []})}
        self.assertEqual(codes, {Code.UNKNOWN_FIELD})

    def test_missing_required_field_is_reported(self) -> None:
        payload = make_draft()
        del payload["goals"]
        report = build_report(DraftSpec.parse(payload))

        self.assertFalse(report.ok)
        self.assertIn(Code.SPEC_REQUIRED.value, _codes(report))

    def test_optional_trace_fields_may_be_absent(self) -> None:
        payload = make_draft()
        for key in ("pain_points", "risks", "removed_requirement_ids", "supplemental_text"):
            del payload[key]
        for requirement in payload["requirements"]:
            requirement.pop("covers", None)

        spec = DraftSpec.parse(payload)

        self.assertEqual(spec.findings, ())
        self.assertTrue(build_report(spec, known_seqs=(1, 2)).ok)

    def test_duplicate_requirement_ids_are_rejected(self) -> None:
        payload = with_requirement(1, id="FR-01")
        report = build_report(DraftSpec.parse(payload), known_seqs=(1, 2))

        self.assertFalse(report.ok)
        self.assertIn(Code.DUPLICATE_ID.value, _codes(report))


class TraceabilityTest(unittest.TestCase):
    def test_requirement_without_source_is_blocked(self) -> None:
        payload = with_requirement(0, source_seqs=[])
        report = build_report(DraftSpec.parse(payload), known_seqs=(1, 2))

        self.assertFalse(report.ok)
        self.assertIn(Code.SOURCE_MISSING.value, _codes(report))

    def test_source_outside_session_is_blocked(self) -> None:
        payload = with_requirement(0, source_seqs=[99])
        report = build_report(DraftSpec.parse(payload), known_seqs=(1, 2))

        self.assertFalse(report.ok)
        self.assertIn(Code.SOURCE_UNKNOWN.value, _codes(report))

    def test_confirmed_pain_point_needs_coverage(self) -> None:
        payload = make_draft(
            pain_points=[
                {"id": "P-1", "text": "看不到占用", "confirmed": True},
                {"id": "P-2", "text": "改期未通知", "confirmed": True},
            ]
        )
        report = build_report(DraftSpec.parse(payload), known_seqs=(1, 2))

        self.assertFalse(report.ok)
        uncovered = [item for item in report.errors if item.code is Code.REQUIRED_UNCOVERED]
        self.assertEqual(len(uncovered), 1)
        self.assertIn("P-2", uncovered[0].message)

    def test_unconfirmed_pain_point_is_optional(self) -> None:
        payload = make_draft(
            pain_points=[
                {"id": "P-1", "text": "看不到占用", "confirmed": True},
                {"id": "P-2", "text": "改期未通知", "confirmed": False},
            ]
        )
        report = build_report(DraftSpec.parse(payload), known_seqs=(1, 2))

        self.assertTrue(report.ok, report.findings)

    def test_must_handle_risk_needs_observable_acceptance(self) -> None:
        payload = with_requirement(1, acceptance_criteria=[])
        report = build_report(DraftSpec.parse(payload), known_seqs=(1, 2))

        self.assertFalse(report.ok)
        self.assertIn(Code.ACCEPTANCE_MISSING.value, _codes(report))

    def test_unknown_cover_reference_is_blocked(self) -> None:
        payload = with_requirement(0, covers=["P-9"])
        report = build_report(DraftSpec.parse(payload), known_seqs=(1, 2))

        self.assertFalse(report.ok)
        self.assertIn(Code.UNKNOWN_TRACE_ITEM.value, _codes(report))

    def test_traceability_skipped_without_session(self) -> None:
        payload = with_requirement(0, source_seqs=[])
        report = build_report(DraftSpec.parse(payload))

        self.assertTrue(report.ok, report.findings)


class AcceptanceTest(unittest.TestCase):
    def test_duplicate_acceptance_is_blocked(self) -> None:
        payload = with_requirement(
            0, acceptance_criteria=["返回确认记录", "返回确认记录"]
        )
        report = build_report(DraftSpec.parse(payload), known_seqs=(1, 2))

        self.assertFalse(report.ok)
        self.assertIn(Code.ACCEPTANCE_DUPLICATE.value, _codes(report))

    def test_empty_acceptance_is_blocked(self) -> None:
        payload = with_requirement(0, acceptance_criteria=["   "])
        report = build_report(DraftSpec.parse(payload), known_seqs=(1, 2))

        self.assertFalse(report.ok)
        self.assertIn(Code.ACCEPTANCE_MISSING.value, _codes(report))


class IdStabilityTest(unittest.TestCase):
    def test_dropped_requirement_needs_registration(self) -> None:
        parent = make_draft()
        parent["requirements"].append(
            {
                "id": "FR-03",
                "kind": "functional",
                "priority": "should",
                "text": "支持每周重复会议。",
                "acceptance_criteria": ["六周会议冲突两次时只创建四次"],
                "source_seqs": [2],
                "covers": [],
            }
        )
        child = copy.deepcopy(parent)
        child["requirements"] = child["requirements"][:2]

        report = build_report(DraftSpec.parse(child), known_seqs=(1, 2), parent=DraftSpec.parse(parent))
        self.assertFalse(report.ok)
        dropped = [item for item in report.errors if item.code is Code.ID_DROPPED]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].requirement_id, "FR-03")

        child["removed_requirement_ids"] = ["FR-03"]
        report = build_report(DraftSpec.parse(child), known_seqs=(1, 2), parent=DraftSpec.parse(parent))
        self.assertTrue(report.ok, report.findings)

    def test_parent_lookup_from_session(self) -> None:
        parent = make_draft()
        session = make_session(make_draft(), parent=parent)
        draft = session.current_draft

        self.assertIsNotNone(draft)
        self.assertIsNotNone(parent_draft(session.drafts, draft))


if __name__ == "__main__":
    unittest.main()
