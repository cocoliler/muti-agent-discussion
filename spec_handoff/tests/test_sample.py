"""示例正文的导入回归：必须始终是可直接导入的 DesignSpecContent。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from spec_handoff import CONTENT_FIELDS, check_export, content_hash

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "campus-meeting.spec.json"


class SampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SAMPLE.exists(), f"缺少示例文件 {SAMPLE}")
        self.text = SAMPLE.read_text(encoding="utf-8")
        self.content = json.loads(self.text)

    def test_sample_passes_import_checks(self) -> None:
        self.assertEqual(check_export(self.content), ())
        self.assertTrue(self.text.endswith("\n"))
        self.assertNotIn("\ufeff", self.text)

    def test_sample_keeps_contract_shape(self) -> None:
        self.assertEqual(list(self.content), list(CONTENT_FIELDS))
        self.assertEqual(
            [item["id"] for item in self.content["requirements"]],
            [f"R{index:02d}" for index in range(1, 9)],
        )
        for requirement in self.content["requirements"]:
            self.assertTrue(requirement["acceptance_criteria"])
            self.assertIn(requirement["kind"], {"functional", "nonfunctional"})
            self.assertIn(requirement["priority"], {"must", "should", "could"})

    def test_sample_hash_matches_flowstudio_contract(self) -> None:
        # 该值由 FlowStudio `content_hash()` 对同一正文计算得出，用于防止示例被无意改动。
        self.assertEqual(
            content_hash(self.content),
            "66912432ab4f35b338e2b8c06a582722bd920a796879974195206f3ceae60691",
        )


if __name__ == "__main__":
    unittest.main()
