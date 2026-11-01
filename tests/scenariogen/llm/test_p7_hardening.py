"""P7 tests: faithfulness verify stage + cost circuit-breaker (offline)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from src.pricer.config import get_project_root
from src.scenariogen.llm.config import load_llm_settings
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.gateway import BudgetExceededError, InstrumentedChatClient, RawCompletion
from src.scenariogen.llm.phases.verify import verify_scenario
from src.scenariogen.llm.prompts import PromptManager
from src.scenariogen.llm.schema import CalibratedScenario, Citation, DriverLeg
from src.scenariogen.llm.trace import run_trace


class _Backend:
    """Returns a fixed completion string; records whether it was called."""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def create(self, **_kwargs: Any) -> RawCompletion:
        self.calls += 1
        return RawCompletion(self.content, 50, 20, "stop")


def _prompts() -> PromptManager:
    return PromptManager(get_project_root() / "prompts" / "scenariogen")


def _scenario() -> CalibratedScenario:
    return CalibratedScenario(
        name="s",
        legs=[
            DriverLeg(factor="fx:TWD", move=-0.08, origin="macro", citations=[Citation(chunk_id="c1", source="macro")]),
            DriverLeg(factor="eq:GHOST", move=-0.5, origin="equity"),  # no citation
        ],
    )


def _verify(content: str) -> Any:
    return verify_scenario(
        gateway=InstrumentedChatClient(_Backend(content), CostTracker()),
        prompts=_prompts(), settings=load_llm_settings(), calibrated=_scenario(), evidence="evidence text",
    )


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #
def test_verify_drops_unsupported_leg() -> None:
    verdicts = json.dumps({"verdicts": [
        {"factor": "fx:TWD", "verdict": "SUPPORTED"},
        {"factor": "eq:GHOST", "verdict": "UNSUPPORTED", "reason": "no cited evidence"},
    ]})
    res = _verify(verdicts)
    assert res.n_dropped == 1
    assert [leg.factor for leg in res.calibrated.legs] == ["fx:TWD"]
    assert res.calibrated.verification["n_dropped"] == 1


def test_verify_never_drops_all_legs() -> None:
    verdicts = json.dumps({"verdicts": [
        {"factor": "fx:TWD", "verdict": "UNSUPPORTED"},
        {"factor": "eq:GHOST", "verdict": "UNSUPPORTED"},
    ]})
    res = _verify(verdicts)  # guard: dropping everything would break materialize
    assert res.n_dropped == 0
    assert len(res.calibrated.legs) == 2


def test_verify_lenient_on_unparseable_output() -> None:
    res = _verify("sorry, no JSON here")
    assert res.n_dropped == 0
    assert len(res.calibrated.legs) == 2


class _RaisingBackend:
    """Simulates an API/backend error (e.g. a 400) on every call."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **_kwargs: Any) -> RawCompletion:
        self.calls += 1
        raise RuntimeError("400 Bad Request")


def test_verify_lenient_on_backend_error() -> None:
    # A backend/API error in the (non-fatal) verify pass must keep all legs, not kill the scenario.
    backend = _RaisingBackend()
    res = verify_scenario(
        gateway=InstrumentedChatClient(backend, CostTracker()),
        prompts=_prompts(), settings=load_llm_settings(), calibrated=_scenario(), evidence="evidence text",
    )
    assert backend.calls == 1
    assert res.n_dropped == 0
    assert len(res.calibrated.legs) == 2


# --------------------------------------------------------------------------- #
# cost circuit-breaker
# --------------------------------------------------------------------------- #
def test_cost_breaker_raises_and_skips_backend() -> None:
    backend = _Backend("{}")
    gateway = InstrumentedChatClient(backend, CostTracker(), cost_budget_usd=0.001)
    with run_trace("run") as trace:
        trace.total_cost_usd = 0.01  # already over budget
        with pytest.raises(BudgetExceededError):
            gateway.complete(messages=[], model="gpt-5", stage="propose")
    assert backend.calls == 0  # breaker fires before the backend is hit


def test_cost_breaker_allows_under_budget() -> None:
    gateway = InstrumentedChatClient(_Backend("{}"), CostTracker(), cost_budget_usd=5.0)
    with run_trace("run"):
        result = gateway.complete(messages=[], model="gpt-5", stage="propose")
    assert result.outcome == "ok"
