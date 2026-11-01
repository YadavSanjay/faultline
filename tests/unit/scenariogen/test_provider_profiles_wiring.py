"""Wiring for spec llm-provider-profiles: the gateway prices calls through `cost_usd` at the
call's start time, backends route by provider, and the narrative stays fail-soft.

Fakes sit at the provider boundary only (a fake `ChatBackend`, a fake `openai` module).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pytest
from src.common.config.models import ConfigError
from src.scenariogen.llm.config import load_llm_settings
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.gateway import InstrumentedChatClient, OpenAIChatBackend, RawCompletion
from src.scenariogen.llm.profiles import (
    RoutingChatBackend,
    build_chat_backend,
    build_provider_backend,
)

_MONDAY_PEAK = datetime(2026, 10, 19, 2, 0, tzinfo=UTC)
_MONDAY_OFF = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
_FLASH = {"input": 0.00015, "output": 0.0006, "cached_input": 0.000003,
          "peak": {"weekdays_utc": ["01:00-04:00"], "input": 0.0003, "output": 0.0012, "cached_input": 0.000006}}


class _Backend:
    def __init__(self, raw: RawCompletion) -> None:
        self.raw = raw
        self.models: list[str] = []

    def create(self, *, model: str, **_kwargs: Any) -> RawCompletion:
        self.models.append(model)
        return self.raw


def _raw() -> RawCompletion:
    return RawCompletion("ok", 10_000, 1_000, "stop", cached_prompt_tokens=4_000)


@pytest.mark.parametrize(("at", "expected"), [(_MONDAY_PEAK, 0.003024), (_MONDAY_OFF, 0.001512)])
def test_gateway_prices_at_call_start_with_cache(at: datetime, expected: float) -> None:
    # 6000*input + 4000*cached + 1000*output per 1K tokens, at peak or off-peak rates.
    gateway = InstrumentedChatClient(_Backend(_raw()), CostTracker({"deepseek-flash": _FLASH}),
                                     min_wait_s=0.0, clock=lambda: at)
    result = gateway.complete(messages=[], model="deepseek-flash", stage="discover")
    assert result.cost_usd == pytest.approx(expected, rel=1e-12)


def test_cost_tracker_logs_cache_tokens(tmp_path) -> None:
    log = tmp_path / "usage.jsonl"
    tracker = CostTracker({"deepseek-flash": _FLASH}, log_path=log)
    tracker.record_usage(model="deepseek-flash", prompt_tokens=100, completion_tokens=10, stage="s",
                         cached_prompt_tokens=40, at=_MONDAY_OFF)
    assert '"cached_prompt_tokens":40' in log.read_text(encoding="utf-8")


def test_routing_backend_sends_each_model_to_its_provider() -> None:
    a, b = _Backend(_raw()), _Backend(_raw())
    router = RoutingChatBackend({"openai": a, "deepseek": b}, {"gpt-5": "openai", "deepseek-flash": "deepseek"})
    router.create(model="deepseek-flash", messages=[])
    router.create(model="gpt-5", messages=[])
    assert (a.models, b.models) == (["gpt-5"], ["deepseek-flash"])
    with pytest.raises(ConfigError):
        router.create(model="claude-haiku-5-5", messages=[])


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class _OpenAI:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    module = ModuleType("openai")
    module.OpenAI = _OpenAI  # type: ignore[attr-defined]  # fake module attribute
    monkeypatch.setitem(sys.modules, "openai", module)
    return calls


def test_deepseek_backend_uses_its_base_url_and_key(fake_openai: list[dict[str, Any]]) -> None:
    config = load_llm_settings(profile="deepseek", environ={})
    backend = build_provider_backend(config, "deepseek", environ={"DEEPSEEK_API_KEY": "sk-d"})
    assert isinstance(backend, OpenAIChatBackend)
    assert fake_openai[-1] == {"max_retries": 0, "timeout": 600.0, "api_key": "sk-d",
                               "base_url": "https://api.deepseek.com"}


def test_factory_passes_provider_and_effort_to_the_openai_backend(fake_openai: list[dict[str, Any]]) -> None:
    deepseek = build_provider_backend(load_llm_settings(profile="deepseek", environ={}), "deepseek",
                                      environ={"DEEPSEEK_API_KEY": "sk-d"})
    openai = build_provider_backend(load_llm_settings(profile="openai", environ={}), "openai",
                                    environ={"OPENAI_API_KEY": "sk-o"})
    assert deepseek._provider == "deepseek"  # type: ignore[attr-defined]  # backend internals
    assert deepseek._models["deepseek-flash"]["effort"] == "high"  # type: ignore[attr-defined]
    assert openai._provider == "openai"  # type: ignore[attr-defined]
    assert all(m["effort"] is None for m in openai._models.values())  # type: ignore[attr-defined]


def test_anthropic_profile_needs_its_key() -> None:
    config = load_llm_settings(profile="anthropic", environ={})
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_chat_backend(config, environ={})


def test_run_record_carries_profile_and_sdk_versions() -> None:
    record = load_llm_settings(profile="deepseek", environ={}).run_record()
    assert record["profile"] == "deepseek"
    assert record["stages"]["propose"] == {"model": "deepseek-v4-pro", "provider": "deepseek"}
    assert set(record["sdk_versions"]) == {"openai", "anthropic"}


def test_narrator_is_fail_soft_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.scenariogen.narrative import build_narrator

    monkeypatch.setenv("SCENARIOGEN_LLM_PROFILE", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY_FILE", raising=False)
    assert build_narrator() is None
