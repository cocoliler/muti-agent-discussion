"""规格版本 API 交接：幂等键、重试、版本冲突。"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from spec_handoff import (
    ApiCode,
    HandoffError,
    HttpRequest,
    HttpResponse,
    SpecRevisionHandoff,
    SubmissionRecord,
    SubmissionStatus,
    content_hash,
    sign_off,
)
from spec_handoff.tests.support import make_session


class FakeTransport:
    """按序返回预设响应，并记录每次请求。"""

    def __init__(self, *responses: HttpResponse | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("出现计划外的请求")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def created(revision: int = 1) -> HttpResponse:
    body = {
        "id": f"00000000-0000-4000-8000-00000000000{revision}",
        "spec_id": "00000000-0000-4000-8000-0000000000ff",
        "revision": revision,
        "content": {},
        "hash": "0" * 64,
        "created_at": "2026-11-23T08:00:00+00:00",
    }
    return HttpResponse(201, json.dumps(body).encode("utf-8"))


def failure(status: int, code: str, message: str = "失败") -> HttpResponse:
    return HttpResponse(status, json.dumps({"error": {"code": code, "message": message}}).encode("utf-8"))


class HandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        session = make_session()
        result = sign_off(session, draft_id="s-1-v1", input_version=1, now="2026-11-23T08:00:00+00:00")
        assert result.handoff is not None
        self.handoff = result.handoff
        self.sleeps: list[float] = []

    def adapter(self, transport: FakeTransport) -> SpecRevisionHandoff:
        return SpecRevisionHandoff(
            "http://127.0.0.1:8000", transport=transport, sleep=self.sleeps.append
        )

    def test_payload_matches_contract(self) -> None:
        record = SubmissionRecord(handoff=self.handoff, expected_revision=0)
        payload = record.payload

        self.assertEqual(set(payload), {"expected_revision", "content"})
        self.assertEqual(payload["expected_revision"], 0)
        self.assertEqual(content_hash(payload["content"]), self.handoff.content_hash)

    def test_successful_submission(self) -> None:
        transport = FakeTransport(created(1))
        record = self.adapter(transport).create(self.handoff, expected_revision=0)

        result = self.adapter(transport).submit(record)

        self.assertIs(result.status, SubmissionStatus.SUCCEEDED)
        self.assertEqual(result.spec_revision, 1)
        self.assertIsNotNone(result.spec_revision_id)
        request = transport.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertTrue(request.url.endswith("/api/v1/projects/p-1/spec-revisions"))
        self.assertEqual(request.headers["Idempotency-Key"], record.idempotency_key)
        self.assertEqual(json.loads(request.body)["expected_revision"], 0)

    def test_revision_conflict_is_reported_then_rebased(self) -> None:
        transport = FakeTransport(failure(409, "REVISION_CONFLICT", "版本已变化，当前版本为 3"))
        record = self.adapter(transport).create(self.handoff, expected_revision=0)

        result = self.adapter(transport).submit(record)

        self.assertIs(result.status, SubmissionStatus.CONFLICT)
        self.assertEqual(result.error, "版本已变化，当前版本为 3")

        rebased = self.adapter(transport).rebase(result, 3)
        self.assertEqual(rebased.expected_revision, 3)
        self.assertNotEqual(rebased.idempotency_key, record.idempotency_key)
        self.assertIs(rebased.status, SubmissionStatus.PENDING)

    def test_idempotency_conflict_is_fatal(self) -> None:
        transport = FakeTransport(failure(409, "IDEMPOTENCY_CONFLICT"))
        record = self.adapter(transport).create(self.handoff)

        result = self.adapter(transport).submit(record)

        self.assertIs(result.status, SubmissionStatus.FAILED)
        self.assertEqual(len(transport.requests), 1)

    def test_storage_busy_is_retried_with_same_key(self) -> None:
        transport = FakeTransport(failure(503, "STORAGE_BUSY"), created(2))
        record = self.adapter(transport).create(self.handoff, expected_revision=1)

        result = self.adapter(transport).submit(record)

        self.assertIs(result.status, SubmissionStatus.SUCCEEDED)
        self.assertEqual(len(transport.requests), 2)
        keys = {request.headers["Idempotency-Key"] for request in transport.requests}
        self.assertEqual(keys, {record.idempotency_key})
        self.assertEqual(self.sleeps, [1])

    def test_network_error_is_retried(self) -> None:
        transport = FakeTransport(OSError("connection reset"), created(1))
        record = self.adapter(transport).create(self.handoff)

        result = self.adapter(transport).submit(record)

        self.assertIs(result.status, SubmissionStatus.SUCCEEDED)
        self.assertEqual([attempt.code for attempt in result.attempts], ["NETWORK_ERROR", "OK"])

    def test_retries_stop_at_max_attempts(self) -> None:
        transport = FakeTransport(*[failure(503, "STORAGE_BUSY") for _ in range(3)])
        record = self.adapter(transport).create(self.handoff)

        result = self.adapter(transport).submit(record)

        self.assertIs(result.status, SubmissionStatus.FAILED)
        self.assertEqual(len(result.attempts), 3)
        self.assertEqual(self.sleeps, [1, 3])

    def test_validation_failure_is_not_retried(self) -> None:
        transport = FakeTransport(failure(422, "VALIDATION_FAILED", "字段超长"))
        record = self.adapter(transport).create(self.handoff)

        result = self.adapter(transport).submit(record)

        self.assertIs(result.status, SubmissionStatus.FAILED)
        self.assertEqual(len(transport.requests), 1)
        self.assertIn("字段超长", result.error or "")

    def test_tampered_content_is_not_submitted(self) -> None:
        transport = FakeTransport()
        record = self.adapter(transport).create(self.handoff)
        record.handoff = replace(
            self.handoff, content={**self.handoff.content, "product_name": "改过的"}
        )

        result = self.adapter(transport).submit(record)

        self.assertIs(result.status, SubmissionStatus.FAILED)
        self.assertEqual(transport.requests, [])

    def test_latest_revision_reads_first_item(self) -> None:
        body = {
            "items": [
                {"id": "r-3", "revision": 3, "hash": "a" * 64},
                {"id": "r-2", "revision": 2, "hash": "b" * 64},
            ],
            "next_cursor": None,
        }
        transport = FakeTransport(HttpResponse(200, json.dumps(body).encode("utf-8")))

        latest = self.adapter(transport).latest_revision("p-1")

        self.assertEqual(latest.revision, 3)
        self.assertEqual(latest.revision_id, "r-3")
        self.assertTrue(transport.requests[0].url.endswith("/spec-revisions?limit=1"))

    def test_latest_revision_without_history(self) -> None:
        transport = FakeTransport(
            HttpResponse(200, json.dumps({"items": [], "next_cursor": None}).encode("utf-8"))
        )

        self.assertEqual(self.adapter(transport).latest_revision("p-1").revision, 0)

    def test_latest_revision_not_found(self) -> None:
        transport = FakeTransport(failure(404, "NOT_FOUND", "项目不存在"))

        with self.assertRaises(HandoffError) as raised:
            self.adapter(transport).latest_revision("missing")

        self.assertIs(raised.exception.code, ApiCode.NOT_FOUND)

    def test_record_roundtrip(self) -> None:
        record = SubmissionRecord(handoff=self.handoff, expected_revision=2)
        record.status = SubmissionStatus.SUCCEEDED
        record.spec_revision = 3
        record.spec_revision_id = "r-3"
        restored = SubmissionRecord.from_dict(record.as_dict())

        self.assertEqual(restored.expected_revision, 2)
        self.assertIs(restored.status, SubmissionStatus.SUCCEEDED)
        self.assertEqual(restored.spec_revision, 3)
        self.assertEqual(restored.handoff.content_hash, self.handoff.content_hash)
        self.assertEqual(restored.payload, record.payload)

    def test_serialized_record_is_json_safe(self) -> None:
        record = SubmissionRecord(handoff=self.handoff)
        json.dumps(record.as_dict(), ensure_ascii=False)

    def test_success_is_idempotent_locally(self) -> None:
        transport = FakeTransport(created(1))
        record = self.adapter(transport).create(self.handoff)
        adapter = self.adapter(transport)

        adapter.submit(record)
        adapter.submit(record)

        self.assertEqual(len(transport.requests), 1)


if __name__ == "__main__":
    unittest.main()
