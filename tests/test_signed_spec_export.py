import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.discussions import routes
from backend.main import app
from backend.roles.contracts import SPEC_VALIDATOR
from spec_handoff import content_hash, dump_text


class SignedSpecExportTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(TemporaryDirectory())
        self.service = routes.build_service(Path(temp) / "sessions.sqlite")
        self.enterContext(patch.object(routes, "_service", self.service))
        self.enterContext(patch.dict(os.environ, {"DISCUSSION_AGENT_MODE": "simulation"}))
        self.embedded = self.enterContext(TestClient(routes.create_app(self.service, with_agents=True)))
        self.workbench = self.enterContext(TestClient(app))
        result = self.embedded.post(
            f"/api/v1/projects/{uuid4()}/discussions",
            json={"idea": "名称：会议室预约。用户：行政人员。目标：预约会议室。"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        self.assertEqual(result.status_code, 201)
        self.sid = result.json()["id"]
        self.path = f"/api/v1/discussions/{self.sid}"
        state = self.workbench.get(self.path).json()
        self.assertEqual(state["status"], "awaiting_confirmation")
        self.confirm = {"draft_id": state["active_draft"]["draft_id"], "input_version": state["input_version"],
                        "expected_state_version": state["state_version"]}
        self.headers = {"Idempotency-Key": str(uuid4())}

    def assert_download(self):
        url = self.workbench.get(self.path).json()["export_url"]
        self.assertEqual(url, self.path + "/export")
        download = self.workbench.get(url)
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers["content-disposition"])
        content = json.loads(download.content.decode("utf-8"))
        SPEC_VALIDATOR.validate(content)
        self.assertEqual(content, json.loads(self.service.repo.get_handoff(self.sid)["content_json"]))
        # 导出文本必须就是 FlowStudio「导入规格 JSON」读取的那份正文。
        self.assertEqual(download.text, dump_text(content))
        self.assertTrue(download.text.startswith('{\n  "schema_version": "1.0"'))
        self.assertNotIn("source_seqs", download.text)
        self.assertNotIn("\r\n", download.text)
        self.assertEqual(download.headers["x-content-hash"], content_hash(content))
        self.assertLess(len(download.content), 1024 * 1024)

    def test_export_any_draft_before_signoff(self):
        state = self.workbench.get(self.path).json()
        draft_id = state["active_draft"]["draft_id"]

        download = self.workbench.get(f"{self.path}/export?draft_id={draft_id}")

        self.assertEqual(download.status_code, 200)
        content = json.loads(download.content.decode("utf-8"))
        SPEC_VALIDATOR.validate(content)
        self.assertEqual(download.text, dump_text(content))
        self.assertNotIn("source_seqs", download.text)
        self.assertEqual(download.headers["x-content-hash"], content_hash(content))
        # 未签收时默认导出仍然拒绝；未知草稿版本返回 404。
        self.assertEqual(self.workbench.get(self.path + "/export").status_code, 409)
        self.assertEqual(self.workbench.get(self.path + "/export?draft_id=missing").status_code, 404)

    def test_demo_export_is_importable(self):
        sid = str(uuid4())
        self.assertEqual(self.workbench.put(f"/api/demo/{sid}").status_code, 200)
        for stage in range(4):
            advanced = self.workbench.post(
                f"/api/demo/{sid}/advance", json={"expected_stage": stage}
            )
            self.assertEqual(advanced.status_code, 200)
        confirmed = self.workbench.post(f"/api/demo/{sid}/confirm", json={"revision": 2})
        self.assertEqual(confirmed.status_code, 200)

        download = self.workbench.get(f"/api/demo/{sid}/export")
        self.assertEqual(download.status_code, 200)
        content = json.loads(download.content.decode("utf-8"))
        SPEC_VALIDATOR.validate(content)
        self.assertEqual(download.text, dump_text(content))
        self.assertEqual(download.headers["x-content-hash"], content_hash(content))

    def test_workbench_downloads_only_the_signed_spec(self):
        self.assertIsNone(self.workbench.get(self.path).json().get("export_url"))
        self.assertEqual(self.workbench.get(self.path + "/export").status_code, 409)
        result = self.workbench.post(self.path + "/confirm", json=self.confirm, headers=self.headers)
        self.assertTrue(result.json()["decision"]["ok"])
        self.assertEqual(result.json()["handoff"]["export_url"], self.path + "/export")
        self.assert_download()

    def test_previous_signoff_and_replayed_receipt_can_download(self):
        old = self.embedded.post(self.path + "/confirm", json=self.confirm, headers=self.headers)
        self.assertIsNone(old.json()["handoff"]["export_url"])
        self.assertEqual(self.embedded.get(self.path + "/export").status_code, 404)
        self.assert_download()
        replay = self.workbench.post(self.path + "/confirm", json=self.confirm, headers=self.headers)
        self.assertEqual(replay.json()["handoff"]["confirmed_at"], old.json()["handoff"]["confirmed_at"])
        self.assertEqual(replay.json()["handoff"]["export_url"], self.path + "/export")
