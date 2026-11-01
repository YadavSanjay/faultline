"""P0 scaffolding tests for the scenariogen LLM flow (offline — fake backend, no network).

Covers cost tracking, the instrumented gateway (success / retry / fatal / exhaustion / trace
integration), the per-run trace, prompt loading + signature, config loading from the real
config files, the structured schema → ScenarioDefinition bridge, and the artifact store.
"""

from __future__ import annotations

import pytest
from src.pricer.config import get_project_root
from src.scenariogen.llm.config import load_llm_settings, load_mcp_settings, load_pricing_overrides
from src.scenariogen.llm.cost import MODEL_PRICING, CostTracker, estimate_cost
from src.scenariogen.llm.gateway import (
    FatalChatError,
    InstrumentedChatClient,
    OpenAIChatBackend,
    RawCompletion,
    TransientChatError,
)
from src.scenariogen.llm.prompts import PromptManager
from src.scenariogen.llm.schema import CalibratedScenario, Citation, DriverLeg, LLMScenarioArtifact, ScenarioProposal
from src.scenariogen.llm.store import LLMScenarioArtifactStore
from src.scenariogen.llm.trace import current_run, run_trace
from src.scenariogen.scenario_gen import ScenarioDefinition


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeBackend:
    """Scripted ChatBackend: yields RawCompletion(s) or raises queued exceptions in order."""

    def __init__(self, script: list[object]):
        self._script = list(script)
        self.calls = 0

    def create(self, **_kwargs: object) -> RawCompletion:
        item = self._script[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, RawCompletion)
        return item


def _ok(content: str = "{}", pt: int = 1000, ct: int = 500) -> RawCompletion:
    return RawCompletion(content=content, prompt_tokens=pt, completion_tokens=ct, finish_reason="stop")


# --------------------------------------------------------------------------- #
# cost
# --------------------------------------------------------------------------- #
def test_estimate_cost_known_model() -> None:
    # gpt-5: 1000/1000*0.00125 + 500/1000*0.01 = 0.00125 + 0.005
    assert estimate_cost("gpt-5", 1000, 500) == pytest.approx(0.00625)


def test_estimate_cost_unknown_model_raises() -> None:
    # A $0 cost would hide the call from the budget breaker (design D9).
    with pytest.raises(ValueError, match="not-a-model"):
        estimate_cost("not-a-model", 1000, 500)


def test_cost_tracker_accumulates(tmp_path) -> None:
    log = tmp_path / "cost.jsonl"
    tracker = CostTracker(log_path=log)
    tracker.record_usage(model="gpt-5-mini", prompt_tokens=2000, completion_tokens=1000, stage="evidence")
    tracker.record_usage(model="gpt-5-mini", prompt_tokens=1000, completion_tokens=0, stage="discover")
    summary = tracker.summary()
    assert summary["total_calls"] == 2
    assert summary["total_prompt_tokens"] == 3000
    assert summary["per_model"]["gpt-5-mini"]["calls"] == 2
    assert summary["total_cost_usd"] > 0
    assert log.exists() and len(log.read_text(encoding="utf-8").splitlines()) == 2


def test_pricing_table_has_gpt5_family() -> None:
    assert {"gpt-5", "gpt-5-mini", "gpt-5-nano"} <= set(MODEL_PRICING)


# --------------------------------------------------------------------------- #
# gateway
# --------------------------------------------------------------------------- #
def test_gateway_success_returns_result_with_cost() -> None:
    client = InstrumentedChatClient(FakeBackend([_ok()]), CostTracker())
    result = client.complete(messages=[{"role": "user", "content": "hi"}], model="gpt-5", stage="propose")
    assert result.outcome == "ok"
    assert result.total_tokens == 1500
    assert result.cost_usd == pytest.approx(0.00625)
    assert result.attempts == 1 and result.retries == 0


def test_gateway_retries_transient_then_succeeds() -> None:
    backend = FakeBackend([TransientChatError("429"), _ok()])
    client = InstrumentedChatClient(backend, CostTracker(), max_attempts=4, min_wait_s=0.0)
    result = client.complete(messages=[], model="gpt-5-mini", stage="evidence")
    assert result.attempts == 2 and result.retries == 1
    assert backend.calls == 2


def test_gateway_fatal_is_not_retried() -> None:
    backend = FakeBackend([FatalChatError("400")])
    client = InstrumentedChatClient(backend, CostTracker(), min_wait_s=0.0)
    with pytest.raises(FatalChatError):
        client.complete(messages=[], model="gpt-5", stage="propose")
    assert backend.calls == 1


def test_gateway_exhaustion_raises_transient() -> None:
    backend = FakeBackend([TransientChatError("a"), TransientChatError("b")])
    client = InstrumentedChatClient(backend, CostTracker(), max_attempts=2, min_wait_s=0.0)
    with pytest.raises(TransientChatError):
        client.complete(messages=[], model="gpt-5", stage="propose")
    assert backend.calls == 2


def test_gateway_content_filter_is_fatal() -> None:
    blocked = RawCompletion(content="", prompt_tokens=10, completion_tokens=0, finish_reason="content_filter")
    client = InstrumentedChatClient(FakeBackend([blocked]), CostTracker(), min_wait_s=0.0)
    with pytest.raises(FatalChatError):
        client.complete(messages=[], model="gpt-5", stage="propose")


def test_gateway_records_into_active_trace() -> None:
    client = InstrumentedChatClient(FakeBackend([_ok()]), CostTracker())
    with run_trace("run-1") as trace:
        client.complete(messages=[], model="gpt-5", stage="propose")
        assert trace.n_llm_calls == 1
        assert trace.per_stage["propose"].n_calls == 1
        assert trace.total_cost_usd == pytest.approx(0.00625)
    assert current_run() is None  # context exited


def test_openai_backend_classify_status_and_name() -> None:
    class RateLimitError(Exception):
        pass

    class Boom(Exception):
        def __init__(self) -> None:
            super().__init__("boom")
            self.status_code = 503

    assert isinstance(OpenAIChatBackend._classify(RateLimitError()), TransientChatError)
    assert isinstance(OpenAIChatBackend._classify(Boom()), TransientChatError)
    assert isinstance(OpenAIChatBackend._classify(ValueError("bad")), FatalChatError)


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #
def _prompt_manager() -> PromptManager:
    return PromptManager(get_project_root() / "prompts" / "scenariogen")


def test_prompts_load_and_signature_stable() -> None:
    pm = _prompt_manager()
    names = pm.names()
    assert "propose_system" in names and "calibrate_user" in names
    assert pm.signature() == _prompt_manager().signature()
    assert len(pm.signature()) == 12


def test_prompt_render_and_missing_var() -> None:
    pm = _prompt_manager()
    rendered = pm.render("discover_user", portfolio_summary="AI book", tickers="NVDA", asset_classes="equity")
    assert "AI book" in rendered and "NVDA" in rendered
    # var-less system template renders unchanged
    assert pm.render("discover_system") == pm.get("discover_system").text
    with pytest.raises(KeyError):
        pm.render("discover_user", portfolio_summary="x")  # missing tickers/asset_classes


def test_calibrate_template_literal_json_braces_survive() -> None:
    # propose_system embeds a JSON example with doubled braces + a {max_scenarios} slot.
    rendered = _prompt_manager().render("propose_system", max_scenarios=10)
    assert '"scenarios"' in rendered  # the literal JSON example rendered intact
    assert "10" in rendered


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_load_llm_settings() -> None:
    settings = load_llm_settings(environ={})
    assert settings.profile == "openai"
    assert settings.provider_for("propose") == "openai"
    assert settings.model_for("propose") == "gpt-5"
    with pytest.raises(KeyError):
        settings.model_for("unknown_stage")
    assert settings.max_tokens_for("evidence") == 16000
    assert settings.budgets.max_scenarios == 10
    assert settings.budgets.max_candidates == 10


def test_load_mcp_and_pricing() -> None:
    mcp = load_mcp_settings()
    assert set(mcp["servers"]) == {"arag", "macro"}
    assert "evidence" in mcp["phase_allowlists"]
    pricing = load_pricing_overrides()
    assert pricing is not None and "gpt-5" in pricing


# --------------------------------------------------------------------------- #
# schema bridge → existing ScenarioDefinition
# --------------------------------------------------------------------------- #
def test_calibrated_scenario_bridges_to_scenario_definition() -> None:
    cal = CalibratedScenario(
        name="taiwan_tail",
        narrative="Export-control escalation.",
        trigger="BIS rule",
        horizon_days=10,
        legs=[
            DriverLeg(factor="eq:NVDA", move=-0.20, origin="equity",
                      citations=[Citation(chunk_id="arag::1", source="arag")]),
            DriverLeg(factor="fx:TWD", move=-0.08, origin="macro"),
            DriverLeg(factor="credit", move=0.0090, origin="macro"),
        ],
        overlays={"eq:NVDA": -0.05},
        world_view_id="macro-2026-06-18",
    )
    spec = cal.to_definition_spec()
    assert spec["drivers"]["eq:NVDA"] == pytest.approx(-0.20)
    assert spec["method"] == "conditional_covariance"
    # The spec is consumable by the EXISTING scenario machinery (bare floats pass through).
    definition = ScenarioDefinition.from_config("taiwan_tail", spec)
    assert definition.drivers["fx:TWD"] == pytest.approx(-0.08)
    assert definition.overlays["eq:NVDA"] == pytest.approx(-0.05)
    assert definition.horizon_days == 10


# --------------------------------------------------------------------------- #
# store
# --------------------------------------------------------------------------- #
def test_artifact_store_roundtrip(tmp_path) -> None:
    store = LLMScenarioArtifactStore(tmp_path)
    artifact = LLMScenarioArtifact(
        run_id="run-xyz",
        mode="hypothetical",
        models={"propose": "gpt-5"},
        proposals=[ScenarioProposal(name="taiwan_tail", theme="geopolitics")],
    )
    store.write_manifest(artifact)
    store.write_narrative("run-xyz", "taiwan_tail", "# Taiwan Tail\n\nNarrative body.")
    assert store.list_runs() == ["run-xyz"]
    loaded = store.read_manifest("run-xyz")
    assert loaded.mode == "hypothetical"
    assert loaded.proposals[0].name == "taiwan_tail"
    assert (store.run_dir("run-xyz") / "narratives" / "taiwan_tail.md").exists()
