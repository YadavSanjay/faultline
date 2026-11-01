"""Dual-RAG P2 narrative tests: gpt-5 prose over attribution, fail-soft (fake backend)."""

from __future__ import annotations

from typing import Any

from src.scenariogen.attribution import ScenarioAttribution, ServiceContribution
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.gateway import (
    FatalChatError,
    InstrumentedChatClient,
    RawCompletion,
)
from src.scenariogen.narrative import AttributionNarrator, build_narrator


def _attr() -> ScenarioAttribution:
    return ScenarioAttribution(
        scenario="ai_bust",
        authored_baseline={"drivers": {}, "overlays": {}},
        macrorag=ServiceContribution("macrorag", applied=True, status="applied"),
        arag=ServiceContribution("arag", applied=True, status="applied"),
        headline_lines=["MacroRAG set credit = +90bp", "ARag overlay eq:NVDA = -43%"])


class FakeBackend:
    def __init__(self, content: str = "MacroRAG added ... ARag added ...", exc: Exception | None = None):
        self.content, self.exc = content, exc
        self.calls = 0

    def create(self, **kw: Any) -> RawCompletion:
        self.calls += 1
        if self.exc:
            raise self.exc
        return RawCompletion(content=self.content, prompt_tokens=200, completion_tokens=80,
                             finish_reason="stop")


def _narrator(backend: FakeBackend) -> AttributionNarrator:
    tracker = CostTracker()
    client = InstrumentedChatClient(backend, tracker, max_attempts=1)
    return AttributionNarrator(client, tracker, model="gpt-5-mini", max_tokens=6000)


def test_narrative_generated_with_cost():
    n = _narrator(FakeBackend())
    out = n.generate(_attr())
    assert out is not None
    assert out["text"].startswith("MacroRAG added")
    assert out["model"] == "gpt-5-mini"
    assert out["prompt_tokens"] == 200 and out["completion_tokens"] == 80
    assert out["cost_usd"] > 0                        # priced via MODEL_PRICING
    assert n.tracker.total_cost_usd == out["cost_usd"]


def test_narrative_failsoft_on_backend_error():
    n = _narrator(FakeBackend(exc=FatalChatError("boom")))
    assert n.generate(_attr()) is None               # fail-soft -> None, run proceeds


def test_narrative_failsoft_on_empty():
    assert _narrator(FakeBackend(content="   ")).generate(_attr()) is None


def test_build_narrator_none_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert build_narrator() is None
