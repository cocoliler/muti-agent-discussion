"""编解码规则测试（实施方案 §5、T1/T2）。"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discussions.codec import CodecError, dump_message_row, dumps, loads  # noqa: E402


class Role(str, Enum):
    USER = "user"
    ARCHITECT = "architect"
    CHALLENGER = "challenger"


class Kind(str, Enum):
    USER_INPUT = "user_input"
    CHALLENGE = "challenge"


class Status(str, Enum):
    CLARIFYING = "clarifying"


@dataclass(frozen=True)
class Msg:
    seq: int
    role: Role
    kind: Kind
    text: str
    source_seqs: tuple[int, ...] = ()
    reply_to_seq: int | None = None
    item_refs: tuple[str, ...] = ()
    round_no: int = 1


@dataclass
class Session:
    session_id: str
    status: Status = Status.CLARIFYING
    messages: list[Msg] = field(default_factory=list)
    named_responses: dict[Role, tuple[str, ...]] = field(default_factory=dict)
    covered_statements: dict[str, set[Role]] = field(default_factory=dict)
    paused_from: Status | None = None
    draft_content: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class SessionWithNewField:
    """模拟成员 1 新增字段：旧快照应能按默认值加载。"""

    session_id: str
    status: Status = Status.CLARIFYING
    dispatch_units: int = 0
    messaging_budget: int = 64


class CodecRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Session(
            session_id="s-1",
            messages=[
                Msg(seq=1, role=Role.USER, kind=Kind.USER_INPUT, text="做一个会议室预约工具"),
                Msg(seq=2, role=Role.CHALLENGER, kind=Kind.CHALLENGE, text="并发会重复占用吗？",
                    source_seqs=(1,), reply_to_seq=1, item_refs=("FR-02",)),
            ],
            named_responses={Role.ARCHITECT: ("I-1", "I-2")},
            covered_statements={"T1": {Role.ARCHITECT, Role.CHALLENGER}},
            draft_content={"requirements": [{"id": "FR-01"}]},
        )

    def test_roundtrip_is_deep_equal(self) -> None:
        restored = loads(dumps(self.session), Session)
        self.assertEqual(restored, self.session)

    def test_tuple_fields_restore_as_tuple(self) -> None:
        restored = loads(dumps(self.session), Session)
        self.assertIsInstance(restored.messages[1].source_seqs, tuple)
        self.assertIsInstance(restored.messages[1].item_refs, tuple)
        self.assertEqual(restored.messages[1].source_seqs, (1,))

    def test_set_fields_restore_as_set(self) -> None:
        restored = loads(dumps(self.session), Session)
        self.assertEqual(restored.covered_statements["T1"], {Role.ARCHITECT, Role.CHALLENGER})
        self.assertIsInstance(restored.covered_statements["T1"], set)

    def test_enum_dict_keys_restore_as_enum(self) -> None:
        restored = loads(dumps(self.session), Session)
        self.assertIn(Role.ARCHITECT, restored.named_responses)
        self.assertEqual(restored.named_responses[Role.ARCHITECT], ("I-1", "I-2"))

    def test_frozen_dataclass_stays_hashable(self) -> None:
        restored = loads(dumps(self.session), Session)
        self.assertEqual(hash(restored.messages[0]), hash(self.session.messages[0]))
        self.assertEqual({restored.messages[0], self.session.messages[0]}, {restored.messages[0]})

    def test_enum_values_are_lowercase_strings(self) -> None:
        raw = dumps(self.session)
        self.assertIn('"role":"challenger"', raw)
        self.assertIn('"status":"clarifying"', raw)


class CodecCompatibilityTest(unittest.TestCase):
    def test_missing_field_uses_default(self) -> None:
        raw = dumps(Session(session_id="s-2"))
        upgraded = loads(raw, SessionWithNewField)
        self.assertEqual(upgraded.session_id, "s-2")
        self.assertEqual(upgraded.dispatch_units, 0)
        self.assertEqual(upgraded.messaging_budget, 64)

    def test_unknown_field_is_preserved(self) -> None:
        raw = '{"codec_version": 1, "data": {"session_id": "s-3", "future_flag": true}}'
        loaded = loads(raw, Session)
        self.assertTrue(getattr(loaded, "_codec_extra")["future_flag"])
        self.assertIn("future_flag", dumps(loaded))

    def test_unknown_enum_value_reports_path(self) -> None:
        raw = '{"codec_version": 1, "data": {"session_id": "s-4", "status": "archived"}}'
        with self.assertRaises(CodecError) as ctx:
            loads(raw, Session)
        self.assertIn("status", str(ctx.exception))

    def test_newer_codec_version_rejected(self) -> None:
        raw = '{"codec_version": 99, "data": {"session_id": "s-5"}}'
        with self.assertRaises(CodecError):
            loads(raw, Session)

    def test_corrupt_payload_rejected(self) -> None:
        with self.assertRaises(CodecError):
            loads("not json", Session)


class MessageRowTest(unittest.TestCase):
    def test_projection_row_matches_schema_order(self) -> None:
        message = Msg(seq=7, role=Role.CHALLENGER, kind=Kind.CHALLENGE, text="证据？",
                      source_seqs=(5, 6), reply_to_seq=5, item_refs=("FR-02",), round_no=2)
        row = dump_message_row(message, "s-9", "2026-11-23T00:00:00Z")
        self.assertEqual(len(row), 15)
        self.assertEqual(row[-1], '{}')
        self.assertEqual(row[0], "s-9")
        self.assertEqual(row[1], 7)
        self.assertEqual(row[2], "challenger")
        self.assertEqual(row[8], "[5, 6]")
        self.assertEqual(row[12], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
