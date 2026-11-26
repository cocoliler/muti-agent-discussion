"""在一次讨论会话内执行角色任务；轮次、状态迁移和持久化由协调服务管理。"""

import asyncio
import hashlib
import json
import math
from time import perf_counter
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

from .contracts import Finding, NewFinding, OptionScoreInput, OptionScores, Reassessment, RoleRequest, RoleRuleError, build_messages, output_model, validate_output
from .provider import Completion, ProviderError


class Provider(Protocol):
    async def complete(self, request: RoleRequest, messages: list[dict]) -> Completion: ...


class Attempt(BaseModel):
    attempt_no: int
    status: Literal["succeeded", "failed", "cancelled"]
    elapsed_seconds: float
    usage: dict[str, int | None] | None
    error: str | None
    diagnostic: str | None = None


class RoleResult(BaseModel):
    logical_task_id: str
    session_id: str
    role: str
    input_version: int
    snapshot_ref: str
    mode: Literal["simulation", "real"]
    model: str | None = None
    output: dict | None = None
    error: str | None = None
    attempts: list[Attempt]


class RoleRunner:
    """一个会话共享一个实例，所有调用从同一个事件循环发起。"""

    MAX_ATTEMPTS = 2

    def __init__(self, session_id: str, provider: Provider, *, mode: Literal["simulation", "real"],
                 call_limit: int = 32, timeout_seconds: float = 45):
        if type(call_limit) is not int or call_limit < 1:
            raise ValueError("call_limit must be a positive integer")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if mode not in ("simulation", "real"):
            raise ValueError("unknown provider mode")
        self.session_id = session_id
        self.provider = provider
        self.mode = mode
        self.call_limit = call_limit
        self.timeout_seconds = timeout_seconds
        self.calls_used = 0
        self._completed: dict[str, RoleResult] = {}
        self._active: set[str] = set()

    def extend_budget(self, extra_calls: int) -> None:
        """编排端收到用户授权后增加额度，保留所有历史消耗。"""
        if type(extra_calls) is not int or extra_calls < 1:
            raise ValueError("extra_calls must be a positive integer")
        self.call_limit += extra_calls

    async def run(self, request: RoleRequest, *, validation_diagnostic: str | None = None) -> RoleResult:
        if request.session_id != self.session_id:
            raise ValueError("request belongs to a different discussion")
        request = request.model_copy(deep=True)
        snapshot = json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(snapshot.encode()).hexdigest()
        task_id = request.logical_task_id
        result = RoleResult(logical_task_id=task_id, session_id=self.session_id, role=request.role,
                            input_version=request.input_version, snapshot_ref=digest,
                            mode=self.mode, model=getattr(self.provider, "model", None), attempts=[])
        previous = self._completed.get(task_id)
        if previous is not None and previous.snapshot_ref != digest:
            result.error = "task_snapshot_conflict"
            return result
        if task_id in self._active:
            result.error = "task_in_progress"
            return result
        if previous is not None:
            if previous.error != "call_limit" or self.calls_used >= self.call_limit:
                return previous.model_copy(deep=True)
            result = previous.model_copy(deep=True)
            if previous.attempts and previous.attempts[-1].error == "invalid_output":
                validation_diagnostic = previous.attempts[-1].diagnostic

        messages = build_messages(request)
        self._active.add(task_id)
        try:
            for number in range(len(result.attempts) + 1, self.MAX_ATTEMPTS + 1):
                # 在第一次 await 之前预留；并行角色和传输重试共享会话额度。
                if self.calls_used >= self.call_limit:
                    result.error = "call_limit"
                    break
                self.calls_used += 1
                start = perf_counter()
                usage = None
                error = None
                diagnostic = None
                retryable = False
                try:
                    async with asyncio.timeout(self.timeout_seconds):
                        prompt = messages
                        if validation_diagnostic:
                            # 反馈仅包含服务端校验诊断，原任务快照和冻结稿保持一致。
                            feedback = {"role": "user", "content": json.dumps(
                                {"previous_validation_error": validation_diagnostic}, ensure_ascii=False)}
                            prompt = [messages[0], feedback, messages[1]]
                        response = await self.provider.complete(request, prompt)
                    usage = response.usage
                    try:
                        output = validate_output(request, response.content)
                        result.output = output.model_dump(mode="json")
                    except ValidationError as exc:
                        error = "invalid_output"
                        first = exc.errors(include_input=False, include_context=False)[0]
                        diagnostic = first["type"]
                        location = first["loc"]
                        legacy_finding = request.role == "reviewer" and location and location[0] == "findings"
                        if location and (location[0] in output_model(request).model_fields or legacy_finding):
                            diagnostic += " at " + location[0]
                            review_fields = {"findings": Finding, "new_findings": NewFinding, "reassessments": Reassessment}
                            if request.role == "reviewer" and location[0] in review_fields and len(location) >= 3:
                                index, field = location[1:3]
                                if isinstance(index, int) and field in review_fields[location[0]].model_fields:
                                    diagnostic += f"/{index}/{field}"
                            if request.kind == "scoring" and location[:2] == ("scorecard", "options"):
                                diagnostic += "/options"
                                if len(location) > 2 and isinstance(location[2], int):
                                    diagnostic += f"/{location[2]}"
                                    if len(location) > 3 and location[3] in OptionScoreInput.model_fields:
                                        diagnostic += f"/{location[3]}"
                                        if location[3] == "scores" and len(location) > 4 and location[4] in OptionScores.model_fields:
                                            diagnostic += f"/{location[4]}"
                    except RoleRuleError as exc:
                        error, diagnostic = "invalid_output", str(exc)
                    except json.JSONDecodeError as exc:
                        error, diagnostic = "invalid_output", f"invalid_json at line {exc.lineno} column {exc.colno}"
                    except ValueError:
                        error = "invalid_output"
                        diagnostic = "contract_rule"
                except TimeoutError:
                    error, retryable = "timeout", True
                except ProviderError as exc:
                    error, retryable, usage = exc.code, exc.retryable, exc.usage
                except asyncio.CancelledError:
                    error = "cancelled"
                    raise
                except Exception:
                    # 第三方 Provider 意外出错时不泄露请求内容，也保留已消耗的调用。
                    error = "internal_error"
                finally:
                    status = "succeeded" if error is None else "failed"
                    if error == "cancelled":
                        status = "cancelled"
                    result.attempts.append(Attempt(attempt_no=number, status=status, elapsed_seconds=perf_counter() - start,
                                                   usage=usage, error=error, diagnostic=diagnostic))
                    result.error = error
                if error == "invalid_output":
                    validation_diagnostic = diagnostic
                    retryable = self.calls_used < self.call_limit
                if not retryable:
                    break
            return result
        finally:
            self._active.remove(task_id)
            if result.error or result.output is not None:
                self._completed[task_id] = result.model_copy(deep=True)
