"""Wiring for spec llm-anthropic-backend: the factory builds the Anthropic backend from the model
registry, refusals are logged with their category, and structured stages pick their JSON mode by
the model's registry `json_mode`.

Fakes sit at the provider boundary only (a fake `anthropic` module, a fake `ChatBackend`).
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from src.pricer.config import get_project_root
from src.scenariogen.llm.anthropic_backend import AnthropicChatBackend
from src.scenariogen.llm.config import load_llm_settings
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.gateway import InstrumentedChatClient, RawCompletion
from src.scenariogen.llm.phases.json_schemas import PROPOSE_SCHEMA, VERIFY_SCHEMA, response_format_for
from src.scenariogen.llm.phases.propose import propose_scenarios
from src.scenariogen.llm.profiles import build_chat_backend
from src.scenariogen.llm.prompts import PromptManager


@pytest.fixture
def fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class _Anthropic:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    module = ModuleType("anthropic")
    module.Anthropic = _Anthropic  # type: ignore[attr-defined]  # fake module attribute
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return calls


def test_factory_builds_anthropic_backend_from_registry(fake_anthropic: list[dict[str, Any]]) -> None:
    config = load_llm_settings(profile="anthropic", environ={})
    router = build_chat_backend(config, environ={"ANTHROPIC_API_KEY": "sk-a"})
    backend = router._backends["anthropic"]  # type: ignore[attr-defined]  # RoutingChatBackend internals
    assert isinstance(backend, AnthropicChatBackend)
    assert fake_anthropic[-1] == {"max_retries": 0, "timeout": 600.0, "api_key": "sk-a"}
    assert backend._models["claude-sonnet-5-5"] == {"max_output_tokens": 128000, "effort": "high",
                                                    "thinking": "adaptive"}
    assert backend._models["claude-haiku-5-5"]["effort"] == "medium"
    assert set(backend._models) == {"claude-sonnet-5-5", "claude-haiku-5-5"}


def test_refusal_logs_its_category(caplog: pytest.LogCaptureFixture) -> None:
    final = SimpleNamespace(content=[], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"),
                            usage=SimpleNamespace(input_tokens=5, output_tokens=0), model="claude-sonnet-5-5")
    with caplog.at_level(logging.WARNING, logger="scenariogen.llm.anthropic"):
        raw = AnthropicChatBackend._completion(final)
    assert raw.finish_reason == "refusal"
    assert "category=cyber" in caplog.text


def test_response_format_follows_the_registry_json_mode() -> None:
    anthropic = load_llm_settings(profile="anthropic", environ={})
    openai = load_llm_settings(profile="openai", environ={})
    assert response_format_for(anthropic, "propose", "p", PROPOSE_SCHEMA) == {
        "type": "json_schema", "json_schema": {"name": "p", "schema": PROPOSE_SCHEMA, "strict": True}}
    assert response_format_for(openai, "verify", "v", VERIFY_SCHEMA) == {"type": "json_object"}


def _closed(schema: dict[str, Any]) -> bool:
    """Every object closed with all properties required, as structured outputs need."""
    if schema.get("type") == "object":
        props = schema["properties"]
        if schema.get("additionalProperties") is not False or set(schema["required"]) != set(props):
            return False
        return all(_closed(p) for p in props.values())
    if schema.get("type") == "array":
        return _closed(schema["items"])
    return True


def test_structured_schemas_are_closed() -> None:
    assert _closed(PROPOSE_SCHEMA)
    assert _closed(VERIFY_SCHEMA)


class _Recorder:
    def __init__(self, content: str) -> None:
        self.content = content
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> RawCompletion:
        self.kwargs = kwargs
        return RawCompletion(self.content, 10, 5, "stop")


def test_propose_sends_its_schema_under_the_anthropic_profile() -> None:
    settings = load_llm_settings(profile="anthropic", environ={})
    backend = _Recorder('{"scenarios": [{"name": "taiwan", "theme": "", "narrative": "", "trigger": "",'
                        ' "method": "conditional_covariance", "horizon_days": 10, "driver_factors": [],'
                        ' "exposed_trades": [], "rationale": ""}]}')
    gateway = InstrumentedChatClient(backend, CostTracker(settings.pricing), min_wait_s=0.0)
    proposals = propose_scenarios(
        gateway=gateway, prompts=PromptManager(get_project_root() / "prompts" / "scenariogen"),
        settings=settings, intent="taiwan", portfolio_summary="book", evidence="e", max_scenarios=1)
    assert [p.name for p in proposals] == ["taiwan"]
    assert backend.kwargs["model"] == "claude-sonnet-5-5"
    assert backend.kwargs["response_format"]["json_schema"]["schema"] == PROPOSE_SCHEMA
