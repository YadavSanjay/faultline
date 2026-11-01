"""Instrumented OpenAI chat gateway (P0).

Mirrors ARag's `InstrumentedChatClient`: the single LLM call-site for the whole scenariogen LLM
flow. Every stage routes through `InstrumentedChatClient.complete`, which times the call,
retries only *transient* failures with exponential backoff, prices the usage via `CostTracker`,
records it into the active `RunTrace`, and returns a rich `LLMCallResult`.

The actual provider call sits behind a `ChatBackend` Protocol so the gateway is fully testable
offline with a fake backend — the heavy `openai` import is deferred into `OpenAIChatBackend`
construction (same pattern as the MacroRAG `RequestsTransport`).
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from ..trace.capture import record_llm_call
from .cost import CostTracker
from .trace import current_run

logger = logging.getLogger("scenariogen.llm.gateway")

# OpenAI error classes that warrant a retry, by class name (the SDK is an optional import, so we
# classify by name/status rather than `except openai.RateLimitError`).
_TRANSIENT_EXC_NAMES = frozenset(
    {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError", "APIStatusError"}
)
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429})

# Finish reasons that end a call: fatal ones are never retried (no model fallback, design D6);
# transient ones are retried like a TransientChatError.
_FATAL_FINISH_REASONS = frozenset({"content_filter", "refusal", "aborted"})
_TRANSIENT_FINISH_REASONS = frozenset({"insufficient_system_resource"})

# A dated snapshot of a model, as OpenAI reports it (`gpt-5` served as `gpt-5-2025-08-07`).
_SNAPSHOT_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}")

DEFAULT_TIMEOUT_S = 600.0  # per-request SDK timeout until provider config supplies one


class ChatBackendError(RuntimeError):
    """Base class for chat-backend failures."""


class TransientChatError(ChatBackendError):
    """A retryable failure (network / rate-limit / 5xx).

    Attributes:
        retry_after_s: The wait the provider asked for (a 429's `retry-after`), or None.
    """

    def __init__(self, message: str, *, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class FatalChatError(ChatBackendError):
    """A non-retryable failure (auth / bad request / content filter)."""


class BudgetExceededError(RuntimeError):
    """The active run's accumulated LLM cost hit the configured ceiling — a runaway backstop."""


@dataclass(frozen=True)
class ToolCall:
    """A function/tool call the model requested (provider-agnostic)."""

    id: str
    name: str
    arguments: str  # raw JSON string as emitted by the model


@dataclass(frozen=True)
class RawCompletion:
    """A backend's normalized completion (provider-agnostic)."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    tool_calls: tuple[ToolCall, ...] = ()
    # The assistant turn as the provider returned it, ready to append to the next request; None
    # when the backend doesn't supply one (the loop then rebuilds it from content + tool calls).
    assistant_message: dict[str, Any] | None = None
    cached_prompt_tokens: int = 0  # included in prompt_tokens
    cache_write_tokens: int = 0  # included in prompt_tokens
    served_model: str = ""  # the model the response names; "" when it names none


@dataclass(frozen=True)
class LLMCallResult:
    """The instrumented result of one `complete` call."""

    content: str
    model: str
    stage: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: float
    attempts: int
    retries: int
    finish_reason: str
    outcome: str
    tool_calls: tuple[ToolCall, ...] = ()
    assistant_message: dict[str, Any] | None = None
    cached_prompt_tokens: int = 0
    cache_write_tokens: int = 0


class ChatBackend(Protocol):
    """Minimal chat-completion surface the gateway needs; fakeable in tests."""

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> RawCompletion: ...


def openai_client_kwargs(*, api_key: str | None, base_url: str | None, timeout_s: float) -> dict[str, Any]:
    """Keyword arguments for constructing the OpenAI SDK client.

    The gateway owns the single retry policy, so the SDK's own retries are always disabled.

    Args:
        api_key: The API key, or None to leave it to the SDK's environment lookup.
        base_url: The API base URL, or None for the SDK default.
        timeout_s: The per-request timeout in seconds; a finite number > 0.

    Returns:
        ``{"max_retries": 0, "timeout": timeout_s}``, plus ``"api_key"`` and ``"base_url"`` for
        each of them that is not None, and no other keys.

    Raises:
        ValueError: If ``timeout_s`` is not a finite number > 0.
    """
    if not (math.isfinite(timeout_s) and timeout_s > 0):
        raise ValueError(f"timeout_s must be a finite number > 0, got {timeout_s!r}")
    kwargs: dict[str, Any] = {"max_retries": 0, "timeout": timeout_s}
    if api_key is not None:
        kwargs["api_key"] = api_key
    if base_url is not None:
        kwargs["base_url"] = base_url
    return kwargs


def reasoning_extra_body(provider: str, effort: str | None) -> dict[str, Any] | None:
    """The ``extra_body`` that sends a model's reasoning effort to an OpenAI-compatible provider.

    Only DeepSeek takes an effort over the OpenAI-compatible API (design §5.3); OpenAI requests stay
    as they are.

    Args:
        provider: The provider name from the model registry (``openai``, ``deepseek`` ...).
        effort: The model's configured effort, or None when the registry sets none.

    Returns:
        ``{"reasoning_effort": effort}`` when ``provider`` is ``"deepseek"`` and ``effort`` is not
        None; None otherwise.
    """
    if provider == "deepseek" and effort is not None:
        return {"reasoning_effort": effort}
    return None


def _cached_prompt_tokens(usage: Any) -> int:
    """Cached prompt tokens: OpenAI's `prompt_tokens_details.cached_tokens`, else DeepSeek's
    `prompt_cache_hit_tokens`, else 0."""
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    if cached is None:
        cached = getattr(usage, "prompt_cache_hit_tokens", None)
    return int(cached or 0)


def _strip_private_keys(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of `messages` without the top-level keys that start with `_` (e.g. `_native`)."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


def _assistant_message_from(message: Any, tool_calls: tuple[ToolCall, ...]) -> dict[str, Any] | None:
    """The assistant turn to send back after a tool-call response, or None without tool calls.

    Keeps the provider's `reasoning_content` (DeepSeek) so the next request carries it.
    """
    if not tool_calls:
        return None
    turn: dict[str, Any] = {
        "role": "assistant",
        "content": getattr(message, "content", None) or None,
        "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in tool_calls
        ],
    }
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning is not None:
        turn["reasoning_content"] = reasoning
    return turn


def _retry_after_s(exc: Exception) -> float | None:
    """The `retry-after` header of an SDK error in seconds, or None if absent or unparseable."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not isinstance(headers, Mapping):
        return None
    value = headers.get("retry-after")
    try:
        seconds = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _is_same_model(requested: str, served: str) -> bool:
    """True when `served` is `requested` or a dated snapshot of it (`<requested>-YYYY-MM-DD`)."""
    if served == requested:
        return True
    return served.startswith(requested) and _SNAPSHOT_SUFFIX.fullmatch(served[len(requested):]) is not None


class OpenAIChatBackend:
    """`openai`-backed chat backend. The SDK import is deferred to construction.

    Maps OpenAI SDK exceptions to `TransientChatError` / `FatalChatError` so the gateway's retry
    logic stays provider-agnostic. The client is `Any` because the SDK ships no usable stubs for
    strict typing and is an optional dependency. The SDK's own retries are off (design D3): the
    gateway is the only retry policy.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        provider: str = "openai",
        models: Mapping[str, Mapping[str, Any]] | None = None,
    ):
        """Builds the backend.

        Args:
            client: An OpenAI SDK client; when None, one is built with the SDK's retries off.
            api_key: The API key, or None to leave it to the SDK's environment lookup.
            base_url: The API base URL (DeepSeek's for the `deepseek` provider), or None.
            timeout_s: The per-request timeout in seconds.
            provider: The registry provider this backend serves (`openai` or `deepseek`).
            models: Per model name, its registry options (`effort`), sent as DeepSeek's
                `reasoning_effort`.
        """
        if client is None:
            import importlib

            openai = importlib.import_module("openai")
            client = openai.OpenAI(**openai_client_kwargs(api_key=api_key, base_url=base_url, timeout_s=timeout_s))
        self._client: Any = client
        self._provider = provider
        self._models = {name: dict(spec) for name, spec in (models or {}).items()}

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> RawCompletion:
        kwargs: dict[str, Any] = {"model": model, "messages": _strip_private_keys(messages)}
        if max_completion_tokens is not None:
            kwargs["max_completion_tokens"] = max_completion_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format is not None:
            kwargs["response_format"] = response_format
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        extra_body = reasoning_extra_body(self._provider, self._models.get(model, {}).get("effort"))
        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Any SDK error is re-raised as a classified ChatBackendError for the gateway to act on.
            raise self._classify(exc) from exc
        choice = resp.choices[0]
        usage = resp.usage
        raw_calls = getattr(choice.message, "tool_calls", None) or []
        tool_calls = tuple(
            ToolCall(
                id=str(getattr(tc, "id", "") or ""),
                name=str(getattr(tc.function, "name", "") or ""),
                arguments=str(getattr(tc.function, "arguments", "") or ""),
            )
            for tc in raw_calls
        )
        return RawCompletion(
            content=str(getattr(choice.message, "content", "") or ""),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            finish_reason=str(getattr(choice, "finish_reason", "") or ""),
            tool_calls=tool_calls,
            assistant_message=_assistant_message_from(choice.message, tool_calls),
            cached_prompt_tokens=_cached_prompt_tokens(usage),
            served_model=str(getattr(resp, "model", "") or ""),
        )

    @staticmethod
    def _classify(exc: Exception) -> ChatBackendError:
        name = type(exc).__name__
        status = getattr(exc, "status_code", None)
        transient_status = isinstance(status, int) and (status in _TRANSIENT_STATUS or status >= 500)
        if name in _TRANSIENT_EXC_NAMES or transient_status:
            return TransientChatError(f"{name}: {exc}", retry_after_s=_retry_after_s(exc))
        return FatalChatError(f"{name}: {exc}")


class InstrumentedChatClient:
    """Shared, instrumented gateway over a `ChatBackend`."""

    def __init__(
        self,
        backend: ChatBackend,
        cost_tracker: CostTracker,
        *,
        max_attempts: int = 4,
        min_wait_s: float = 1.0,
        max_wait_s: float = 30.0,
        cost_budget_usd: float | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        """Builds the gateway.

        Args:
            backend: The provider backend.
            cost_tracker: Prices every successful call.
            max_attempts: Attempts per call, including the first.
            min_wait_s: First backoff wait between attempts.
            max_wait_s: Backoff cap; also caps a provider's `retry-after`.
            cost_budget_usd: The run's cost ceiling, checked before each call.
            clock: Returns the timezone-aware time a call starts, which picks time-of-day rates
                (design D10); `datetime.now(UTC)` when None.
        """
        self._clock = clock or (lambda: datetime.now(UTC))
        self.backend = backend
        self.cost_tracker = cost_tracker
        self.max_attempts = max(1, max_attempts)
        self.min_wait_s = min_wait_s
        self.max_wait_s = max_wait_s
        self.cost_budget_usd = cost_budget_usd

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        stage: str,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMCallResult:
        run = current_run()
        if self.cost_budget_usd is not None and run is not None and run.total_cost_usd >= self.cost_budget_usd:
            raise BudgetExceededError(
                f"run cost ${run.total_cost_usd:.4f} reached budget ${self.cost_budget_usd:.2f}"
                f" before stage {stage!r}"
            )
        attempts = 0
        retries = 0
        wait = self.min_wait_s
        start = time.perf_counter()
        started_at = self._clock()
        last_exc: TransientChatError | None = None
        while attempts < self.max_attempts:
            attempts += 1
            try:
                raw = self.backend.create(
                    model=model,
                    messages=messages,
                    max_completion_tokens=max_completion_tokens,
                    temperature=temperature,
                    response_format=response_format,
                    tools=tools,
                    tool_choice=tool_choice,
                )
                self._check(raw, model, stage)
            except TransientChatError as exc:
                last_exc = exc
                if attempts >= self.max_attempts:
                    break
                retries += 1
                # A provider-requested wait (retry-after) replaces the backoff, capped at max_wait_s.
                delay = wait if exc.retry_after_s is None else min(exc.retry_after_s, self.max_wait_s)
                logger.warning(
                    "LLM %s stage=%s transient error (attempt %d/%d, retry in %.1fs): %s",
                    model, stage, attempts, self.max_attempts, delay, exc,
                )
                time.sleep(delay)
                wait = min(wait * 2, self.max_wait_s)
                continue
            except FatalChatError as exc:
                record_llm_call(purpose=stage, model=model, ok=False,
                                latency_ms=(time.perf_counter() - start) * 1000.0,
                                messages=messages, error=str(exc))
                raise
            latency_ms = (time.perf_counter() - start) * 1000.0
            result = self._finish(raw, model, stage, latency_ms, attempts, retries, started_at)
            record_llm_call(purpose=stage, model=model, ok=True, latency_ms=result.latency_ms,
                            messages=messages, content=result.content,
                            prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens, cost_usd=result.cost_usd)
            return result
        record_llm_call(purpose=stage, model=model, ok=False,
                        latency_ms=(time.perf_counter() - start) * 1000.0,
                        messages=messages, error=str(last_exc))
        raise TransientChatError(
            f"stage {stage!r}: exhausted {self.max_attempts} attempts: {last_exc}"
        )

    @staticmethod
    def _check(raw: RawCompletion, model: str, stage: str) -> None:
        """Raises for a completion the flow must not use: a fatal or transient finish, or another model.

        Raises:
            FatalChatError: A blocked, refused or aborted response (never retried and no fallback,
                design D6), or a response from a model other than `model` or its dated snapshot.
            TransientChatError: The provider ran out of capacity (`insufficient_system_resource`).
        """
        if raw.finish_reason in _FATAL_FINISH_REASONS:
            raise FatalChatError(f"stage {stage!r}: response ended with finish_reason={raw.finish_reason!r}")
        if raw.finish_reason in _TRANSIENT_FINISH_REASONS:
            raise TransientChatError(f"stage {stage!r}: finish_reason={raw.finish_reason!r}")
        if raw.served_model and not _is_same_model(model, raw.served_model):
            raise FatalChatError(f"stage {stage!r}: requested model {model!r} but {raw.served_model!r} answered")

    def _finish(
        self,
        raw: RawCompletion,
        model: str,
        stage: str,
        latency_ms: float,
        attempts: int,
        retries: int,
        started_at: datetime,
    ) -> LLMCallResult:
        if raw.finish_reason == "length":
            logger.warning("LLM %s stage=%s truncated (finish_reason=length)", model, stage)
        record = self.cost_tracker.record_usage(
            model=model,
            prompt_tokens=raw.prompt_tokens,
            completion_tokens=raw.completion_tokens,
            stage=stage,
            cached_prompt_tokens=raw.cached_prompt_tokens,
            cache_write_tokens=raw.cache_write_tokens,
            at=started_at,
        )
        run = current_run()
        if run is not None:
            run.record_llm_call(
                stage=stage,
                prompt_tokens=raw.prompt_tokens,
                completion_tokens=raw.completion_tokens,
                cost_usd=record.estimated_cost_usd,
                retries=retries,
            )
        return LLMCallResult(
            content=raw.content,
            model=model,
            stage=stage,
            prompt_tokens=raw.prompt_tokens,
            completion_tokens=raw.completion_tokens,
            total_tokens=raw.prompt_tokens + raw.completion_tokens,
            cost_usd=record.estimated_cost_usd,
            latency_ms=latency_ms,
            attempts=attempts,
            retries=retries,
            finish_reason=raw.finish_reason,
            outcome="ok",
            tool_calls=raw.tool_calls,
            assistant_message=raw.assistant_message,
            cached_prompt_tokens=raw.cached_prompt_tokens,
            cache_write_tokens=raw.cache_write_tokens,
        )
