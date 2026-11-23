"""导出格式：UTF-8、字段顺序、散列与 1 MiB 上限。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from spec_handoff import (
    Code,
    DraftSpec,
    check_export,
    content_hash,
    dump_bytes,
    dump_text,
    order_content,
    write_export,
)
from spec_handoff.tests.support import make_draft


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.content = DraftSpec.parse(make_draft()).to_content()

    def test_dump_is_utf8_json_with_trailing_newline(self) -> None:
        text = dump_text(self.content)

        self.assertTrue(text.endswith("\n"))
        self.assertIn("内部会议室预约", text)
        self.assertNotIn("\\u", text)
        self.assertEqual(json.loads(text)["product_name"], "内部会议室预约")

    def test_field_order_follows_contract(self) -> None:
        ordered = order_content(dict(reversed(list(self.content.items()))))

        self.assertEqual(next(iter(ordered)), "schema_version")
        self.assertEqual(list(ordered)[:3], ["schema_version", "product_name", "background"])
        self.assertEqual(list(ordered["requirements"][0])[0], "id")

    def test_hash_is_stable_and_order_independent(self) -> None:
        shuffled = dict(reversed(list(self.content.items())))

        self.assertEqual(content_hash(self.content), content_hash(shuffled))
        self.assertEqual(len(content_hash(self.content)), 64)

    def test_write_export_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "spec.json"
            write_export(target, self.content)

            raw = target.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(raw.decode("utf-8"), dump_text(self.content))
            self.assertEqual(json.loads(raw.decode("utf-8")), json.loads(dump_text(self.content)))

    def test_unknown_field_is_rejected(self) -> None:
        findings = check_export({**self.content, "risks": []})

        self.assertEqual({finding.code for finding in findings}, {Code.UNKNOWN_FIELD})

    def test_export_size_limit(self) -> None:
        oversized = dict(self.content)
        oversized["background"] = "背" * 400_000

        codes = {finding.code for finding in check_export(oversized)}
        self.assertIn(Code.EXPORT_TOO_LARGE.value, {code.value for code in codes})

    def test_bytes_match_text(self) -> None:
        self.assertEqual(dump_bytes(self.content), dump_text(self.content).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
