"""讨论角色的单次 DeepSeek JSON 请求。重试、总超时和角色输出校验由运行器负责。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import httpx


_CHAT_URL = "https://api.deepseek.com/chat/completions"
MAX_RESPONSE_BYTES = 1024 * 1024
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}")
# 识别 JSON 字符串，检查模型以转义形式回显凭据的情形。
_JSON_STRING = re.compile(r'"(?:[^"\\]++|\\[\s\S])*+"?')
Usage = dict[str, int | None]


@dataclass(frozen=True)
class Completion:
    """角色消息正文，以及服务端实际报告的 token 数。"""

    content: str
    usage: Usage | None = None


class ProviderError(Exception):
    """向运行器暴露可记录的错误分类、重试标记和已知用量。"""

    def __init__(self, code: str, retryable: bool = False, usage: Usage | None = None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.usage = usage


def _reported_usage(value: object) -> Usage | None:
    """只提取非负整数，保留缺项，避免把未知用量计为零。"""
    if not isinstance(value, dict):
        return None
    counts = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        count = value.get(field)
        counts[field] = count if type(count) is int and count >= 0 else None
    return counts


def _http_error(status: int) -> ProviderError:
    if status in (401, 403):
        return ProviderError("auth")
    if status == 408:
        return ProviderError("timeout", retryable=True)
    if status == 429:
        return ProviderError("rate_limit", retryable=True)
    if 500 <= status < 600:
        return ProviderError("server", retryable=True)
    return ProviderError("http_error")


def _echoes_key(text: str, key: str) -> bool:
    if key in text:
        return True
    if "\\" in text:
        for match in _JSON_STRING.finditer(text):
            try:
                value = json.loads(match.group())
            except ValueError:
                continue
            if key in value:
                return True
    return False


class DeepSeekProvider:
    """使用服务端提供的模型名与密钥，向固定官方地址发送一次请求。"""

    def __init__(
        self, *, api_key: str, model: str, max_tokens: int = 2048,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not isinstance(api_key, str) or not api_key or not all(33 <= ord(c) <= 126 for c in api_key):
            raise ValueError("DeepSeek API 凭据缺失或格式无效")
        if not isinstance(model, str) or not _MODEL_NAME.fullmatch(model):
            raise ValueError("DeepSeek 模型名缺失或格式无效")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 393216:
            raise ValueError("max_tokens 超出 DeepSeek 接口范围")
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key
        self._transport = transport

    async def complete(self, request: object, messages: Sequence[Mapping[str, str]]) -> Completion:
        """提交角色提示词并增量读取响应；请求上下文只供上层构建提示词使用。"""
        payload = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept-Encoding": "identity"}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, trust_env=False, follow_redirects=False, timeout=None,
            ) as client:
                async with client.stream("POST", _CHAT_URL, json=payload, headers=headers) as response:
                    if not response.is_success:
                        raise _http_error(response.status_code)
                    # 禁止解压缩放大内存开销；即使缺少长度头也按实际读取量限流。
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ProviderError("invalid_response")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ProviderError("response_too_large")
                        data.extend(chunk)
        except httpx.TimeoutException:
            error = ProviderError("timeout", retryable=True)
        except httpx.RequestError:
            error = ProviderError("network", retryable=True)
        else:
            return self._parse_response(data)
        # 在捕获块外抛出，防止 HTTP 异常链将请求数据带入错误记录。
        raise error

    def _parse_response(self, data: bytearray) -> Completion:
        try:
            envelope = json.loads(data.decode("utf-8"))
        except (ValueError, RecursionError):
            envelope = None
        if not isinstance(envelope, dict):
            raise ProviderError("invalid_response")
        usage = _reported_usage(envelope.get("usage"))
        choices = envelope.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProviderError("invalid_response", usage=usage)
        choice = choices[0]
        finish = choice.get("finish_reason")
        if finish in ("insufficient_system_resource", "aborted"):
            raise ProviderError("server", retryable=True, usage=usage)
        if finish == "length":
            raise ProviderError("output_truncated", usage=usage)
        if finish == "content_filter":
            raise ProviderError("content_filter", usage=usage)
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if finish != "stop" or not isinstance(content, str) or _echoes_key(content, self._api_key):
            raise ProviderError("invalid_response", usage=usage)
        return Completion(content, usage)
