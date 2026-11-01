"""Wiring for spec llm-native-turns: how `OpenAIChatBackend` builds its SDK client, and the
provider turn the gateway hands to the tool loop.

The `openai` module is replaced by a fake in `sys.modules` (the SDK is the I/O boundary), so no
network call and no installed SDK are needed.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.gateway import (
    DEFAULT_TIMEOUT_S,
    InstrumentedChatClient,
    OpenAIChatBackend,
    TransientChatError,
)


class _RecordingOpenAI:
    """Stands in for `openai.OpenAI`; records the constructor kwargs."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingOpenAI]:
    module = ModuleType("openai")
    module.OpenAI = _RecordingOpenAI  # type: ignore[attr-defined]  # fake module attribute
    monkeypatch.setitem(sys.modules, "openai", module)
    return _RecordingOpenAI


def test_backend_builds_client_without_sdk_retries(fake_openai: type[_RecordingOpenAI]) -> None:
    OpenAIChatBackend()
    assert fake_openai.last_kwargs == {"max_retries": 0, "timeout": DEFAULT_TIMEOUT_S}


def test_backend_passes_key_url_and_timeout(fake_openai: type[_RecordingOpenAI]) -> None:
    OpenAIChatBackend(api_key="sk-x", base_url="https://api.deepseek.com", timeout_s=300.0)
    assert fake_openai.last_kwargs == {
        "max_retries": 0, "timeout": 300.0, "api_key": "sk-x", "base_url": "https://api.deepseek.com"}


def test_backend_rejects_bad_timeout(fake_openai: type[_RecordingOpenAI]) -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        OpenAIChatBackend(timeout_s=0.0)


def test_classify_attaches_retry_after() -> None:
    class RateLimitError(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"retry-after": "2.5"})

    err = OpenAIChatBackend._classify(RateLimitError("slow down"))
    assert isinstance(err, TransientChatError)
    assert err.retry_after_s == 2.5


def test_gateway_carries_provider_turn_and_cache_tokens() -> None:
    message = SimpleNamespace(
        content="",
        reasoning_content="r",
        tool_calls=[SimpleNamespace(id="c1", function=SimpleNamespace(name="arag__t", arguments="{}"))],
    )
    resp = SimpleNamespace(
        model="gpt-5-2025-08-07",
        choices=[SimpleNamespace(finish_reason="tool_calls", message=message)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10,
                              prompt_tokens_details=SimpleNamespace(cached_tokens=40)),
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kw: resp)))
    gateway = InstrumentedChatClient(OpenAIChatBackend(client=client), CostTracker(), min_wait_s=0.0)
    result = gateway.complete(messages=[{"role": "user", "content": "q"}], model="gpt-5", stage="evidence")
    assert result.assistant_message == {
        "role": "assistant", "content": None, "reasoning_content": "r",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "arag__t", "arguments": "{}"}}]}
    assert result.cached_prompt_tokens == 40
    assert result.model == "gpt-5"
