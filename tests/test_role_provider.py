"""成员 2 的 DeepSeek 传输契约测试，所有请求由 MockTransport 在本地接收。"""

import asyncio
import json
import unittest

import httpx

from backend.roles.provider import MAX_RESPONSE_BYTES, DeepSeekProvider, ProviderError


KEY = "sk-only-a-local-test"
MESSAGES = [{"role": "system", "content": "Return JSON"}]


def reply(content='{"summary":"讨论摘要"}', usage=None, finish="stop"):
    return {
        "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}],
        "usage": usage,
    }


def provider(handler):
    return DeepSeekProvider(
        api_key=KEY, model="configured-model", max_tokens=321,
        transport=httpx.MockTransport(handler),
    )


class DeepSeekTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_one_json_request_to_official_endpoint_and_preserves_known_usage(self):
        received = []

        def respond(request):
            received.append(request)
            return httpx.Response(200, json=reply(usage={"prompt_tokens": 8, "extra": KEY}))

        result = await provider(respond).complete({"api_url": "https://example.invalid"}, MESSAGES)
        self.assertEqual(len(received), 1)
        request = received[0]
        self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["authorization"], f"Bearer {KEY}")
        body = json.loads(request.content)
        self.assertEqual(body["model"], "configured-model")
        self.assertEqual(body["messages"], MESSAGES)
        self.assertEqual(body["max_tokens"], 321)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertFalse(body["stream"])
        self.assertEqual(json.loads(result.content), {"summary": "讨论摘要"})
        self.assertEqual(result.usage, {"prompt_tokens": 8, "completion_tokens": None, "total_tokens": None})

    async def test_returns_no_usage_for_absent_usage_and_keeps_reported_zero(self):
        without = await provider(lambda _: httpx.Response(200, json=reply())).complete({}, MESSAGES)
        self.assertIsNone(without.usage)
        partial = {"prompt_tokens": False, "completion_tokens": -1, "total_tokens": 0}
        result = await provider(lambda _: httpx.Response(200, json=reply(usage=partial))).complete({}, MESSAGES)
        self.assertEqual(result.usage, {"prompt_tokens": None, "completion_tokens": None, "total_tokens": 0})

    async def test_http_failures_are_sanitized_and_never_retry_or_follow_redirect(self):
        for status, code, retryable in (
            (307, "http_error", False), (401, "auth", False), (408, "timeout", True),
            (429, "rate_limit", True), (503, "server", True), (400, "http_error", False),
        ):
            with self.subTest(status=status):
                requests = []

                def reject(request):
                    requests.append(request)
                    return httpx.Response(status, headers={"location": "https://example.invalid"}, text=KEY)

                with self.assertRaises(ProviderError) as caught:
                    await provider(reject).complete({}, MESSAGES)
                self.assertEqual(len(requests), 1)
                self.assertEqual((caught.exception.code, caught.exception.retryable), (code, retryable))
                self.assertNotIn(KEY, repr(caught.exception))
                self.assertIsNone(caught.exception.__context__)

    async def test_network_failures_drop_raw_exception_and_external_cancellation_propagates(self):
        for error, code in ((httpx.ReadTimeout(KEY), "timeout"), (httpx.ConnectError(KEY), "network")):
            with self.subTest(code=code):
                def fail(request):
                    raise error

                with self.assertRaises(ProviderError) as caught:
                    await provider(fail).complete({}, MESSAGES)
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(caught.exception.retryable)
                self.assertIsNone(caught.exception.__context__)

        async def stalls(request):
            await asyncio.Event().wait()

        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(provider(stalls).complete({}, MESSAGES), timeout=0.03)

    async def test_failure_preserves_usage_and_discards_model_text(self):
        for finish, content, code, retryable in (
            ("length", "partial", "output_truncated", False),
            ("insufficient_system_resource", "", "server", True),
            ("content_filter", "", "content_filter", False),
            ("stop", None, "invalid_response", False),
            ("stop", '{"value":"' + KEY + '"}', "invalid_response", False),
            ("stop", '{"value":"\\u0073k-only-a-local-test"}', "invalid_response", False),
        ):
            with self.subTest(finish=finish, code=code):
                data = reply(content, {"total_tokens": 17}, finish)
                with self.assertRaises(ProviderError) as caught:
                    await provider(lambda _: httpx.Response(200, json=data)).complete({}, MESSAGES)
                self.assertEqual((caught.exception.code, caught.exception.retryable), (code, retryable))
                self.assertEqual(caught.exception.usage["total_tokens"], 17)
                self.assertNotIn(KEY, repr(caught.exception))

    async def test_malformed_envelope_or_encoding(self):
        for body in (b"not JSON", b"null", b'{"choices":[null]}', b"\xff"):
            with self.subTest(body=body):
                with self.assertRaises(ProviderError) as caught:
                    await provider(lambda _: httpx.Response(200, content=body)).complete({}, MESSAGES)
                self.assertEqual(caught.exception.code, "invalid_response")

    async def test_stops_reading_and_closes_response_at_limit(self):
        class Oversize(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b" " * MAX_RESPONSE_BYTES
                yield b"!"
                raise AssertionError("超出限制后不应继续读取")

            async def aclose(self):
                self.closed = True

        stream = Oversize()
        with self.assertRaises(ProviderError) as caught:
            await provider(lambda _: httpx.Response(200, stream=stream)).complete({}, MESSAGES)
        self.assertEqual(caught.exception.code, "response_too_large")
        self.assertTrue(stream.closed)

    def test_rejects_invalid_server_configuration_with_safe_messages(self):
        for key, model, limit in (("", "model", 1), ("key\n", "model", 1), (KEY, " ", 1), (KEY, "model", True)):
            with self.subTest(model=model, limit=limit):
                with self.assertRaises(ValueError) as caught:
                    DeepSeekProvider(api_key=key, model=model, max_tokens=limit)
                self.assertNotIn(KEY, str(caught.exception))
