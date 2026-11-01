"""Unit tests for the layer ablation wiring (scenario-layer-ablation).

Covers what the locked spec tests don't: that ``DualRagEnrichmentProvider.definition_for`` with every
layer is the production rule (it delegates to ``compose_definition``), that a layer subset reaches the
provider correctly, and the runner core (``ablate_scenario``) with fakes at the service (MacroRAG /
ARag providers) and pricing boundaries.
"""

from __future__ import annotations

import numpy as np
import pytest
from src.scenariogen.ablation import COMPOSITION_RULE, LAYERS, compose_definition
from src.scenariogen.ablation_runner import _parse_args, ablate_scenario, floor_hits
from src.scenariogen.arag.provider import AragResolution
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.macro.provider import DriverResolution
from src.scenariogen.scenario_gen import ScenarioGenerator

# The authored baseline these tests were written against (the pre-replay ``ai_bust`` / ``keyman_nvda``), frozen
# here so a change to the committed scenario set doesn't move them.
AUTHORED = {"scenarios": {
    "ai_bust": {"method": "conditional_covariance", "horizon_days": 10,
                "severity": {"lead": "eq:XLK", "direction": "down"},
                "drivers": {"eq:NVDA": {"move_pct": -35}, "eq:XLK": {"move_pct": -30}}, "overlays": {}},
    "keyman_nvda": {"method": "conditional_covariance", "horizon_days": 5,
                    "severity": {"lead": "eq:NVDA", "direction": "down"},
                    "drivers": {"eq:NVDA": {"move_pct": -30}}, "overlays": {"eqvol": {"move_pts": 8}}},
}}


class FakeMacroProvider:
    def __init__(self, resolution: DriverResolution):
        self.resolution = resolution
        self.calls = 0

    def resolve(self, name: str) -> DriverResolution:
        self.calls += 1
        return self.resolution


class FakeAragProvider:
    def __init__(self, resolution: AragResolution):
        self.resolution = resolution
        self.calls = 0

    def resolve(self, name: str) -> AragResolution:
        self.calls += 1
        return self.resolution


def _macro() -> DriverResolution:
    return DriverResolution(
        scenario="ai_bust",
        drivers={"eq:NVDA": -0.35, "eq:XLK": -0.30, "credit": 0.009, "eqvol": 0.05},
        sources={"eq:NVDA": {"origin": "static"}, "eq:XLK": {"origin": "static"},
                 "credit": {"origin": "macrorag", "macro_field": "IG_OAS"},
                 "eqvol": {"origin": "macrorag", "macro_field": "VIX"}},
        used_macro=True, world_view_id="macro-test-wv")


def _arag() -> AragResolution:
    # NVDA: seed -0.35 calibrated to -0.43; AMD: no calibration (seed == calibrated).
    return AragResolution(
        scenario="ai_bust",
        company_drivers={"eq:NVDA": -0.43, "eq:AMD": -0.30},
        sources={"eq:NVDA": {"origin": "arag", "ticker": "NVDA", "seed_move": -0.35},
                 "eq:AMD": {"origin": "arag", "ticker": "AMD", "seed_move": -0.30}},
        used_arag=True)


def _news(mult: float):
    from src.scenariogen.news.gate import NewsGateResult
    from src.scenariogen.news.mapping import NewsLeg
    from src.scenariogen.news.provider import NewsResolution

    gate = NewsGateResult(ok=True, newest_filing="2026-10-01", staleness_days=1,
                          n_names_with_events=3, reason="ok")
    leg = NewsLeg(ticker="NVDA", multiplier=mult, event_score=1.0, tone_z=-1.0, n_events=2,
                  accessions=["acc-1"], item_codes=["2.02"])
    return NewsResolution(as_of="2026-10-06", gate=gate, legs={"NVDA": leg}, live_triggers={})


def _provider(news_mult: float = 1.2) -> tuple[DualRagEnrichmentProvider, FakeMacroProvider, FakeAragProvider]:
    macro, arag = FakeMacroProvider(_macro()), FakeAragProvider(_arag())
    prov = DualRagEnrichmentProvider(scenario_config=AUTHORED, macro_provider=macro, arag_provider=arag,
                                     news_resolution=_news(news_mult))
    return prov, macro, arag


def test_full_layers_are_the_production_definition():
    # Production: severity_scale already carries tier x state; the composition with every layer must
    # give the additive overlay seed*fact*severity*news on NVDA and leave the systematic anchors.
    prov, _, _ = _provider(news_mult=1.2)
    defn, dual = prov.definition_for("ai_bust", severity="adverse", severity_scale=1.5)
    assert defn.drivers == {"eq:XLK": -0.30, "credit": 0.009, "eqvol": 0.05}
    assert defn.overlays["eq:NVDA"] == pytest.approx(-0.43 * 1.5 * 1.2, rel=1e-12)  # exact algebra
    assert defn.overlays["eq:AMD"] == pytest.approx(-0.30 * 1.5, rel=1e-12)          # no news leg
    assert dual.overlay_sources["eq:NVDA"]["mode"] == "overlay"
    assert set(dual.overlay_sources["eq:NVDA"]["calibration"]) == {"news"}
    assert "eq:NVDA" not in dual.driver_sources


def test_definition_for_equals_compose_definition_for_every_subset():
    prov, _, _ = _provider()
    inputs = prov.layer_inputs("ai_bust")
    inputs.pop("macro_res"), inputs.pop("arag_res")
    for k in range(len(LAYERS) + 1):
        for subset in _subsets(k):
            defn, dual = prov.definition_for("ai_bust", severity="mild", severity_scale=1.0,
                                             layers=subset, state_mult=1.3)
            drivers, overlays, scale = compose_definition(layers=subset, tier_scale=1.0, state_mult=1.3,
                                                          **inputs)
            assert defn.drivers == drivers
            assert defn.overlays == overlays
            assert defn.severity_scale == scale == dual.severity_scale


def _subsets(k: int) -> list[frozenset[str]]:
    from itertools import combinations
    return [frozenset(c) for c in combinations(LAYERS, k)]


def test_base_subset_is_the_authored_scenario():
    # ai_bust in the committed scenarios.json: NVDA -35%, XLK -30%, no overlays.
    prov, _, _ = _provider()
    defn, dual = prov.definition_for("ai_bust", severity="mild", severity_scale=1.0, layers=frozenset())
    assert defn.drivers == pytest.approx({"eq:NVDA": -0.35, "eq:XLK": -0.30}, rel=1e-12)
    assert defn.overlays == {}
    assert dual.driver_sources == {"eq:NVDA": {"origin": "authored"}, "eq:XLK": {"origin": "authored"}}


def test_seed_without_facts_and_state_only_when_enabled():
    prov, _, _ = _provider()
    defn, dual = prov.definition_for("ai_bust", severity="mild", severity_scale=1.0,
                                     layers=frozenset({"macrorag", "xbrlrag_narrative"}), state_mult=1.4)
    assert defn.overlays["eq:NVDA"] == pytest.approx(-0.35, rel=1e-12)   # seed, no news, no state
    assert defn.severity_scale == 1.0
    assert "calibration" not in dual.overlay_sources["eq:NVDA"]
    defn2, _ = prov.definition_for("ai_bust", severity="mild", severity_scale=1.0,
                                   layers=frozenset({"state"}), state_mult=1.4)
    assert defn2.severity_scale == pytest.approx(1.4, rel=1e-12)


def test_services_resolved_once_across_subsets():
    prov, macro, arag = _provider()
    for subset in _subsets(2):
        prov.definition_for("ai_bust", severity="mild", severity_scale=1.0, layers=subset)
    assert macro.calls == 1
    assert arag.calls == 1


def _toy_generator() -> ScenarioGenerator:
    factors = ("eq:XLK", "eq:NVDA", "eq:AMD", "credit", "eqvol")
    vols = np.array([0.02, 0.03, 0.03, 0.0005, 0.01])
    corr = np.full((5, 5), 0.3) + 0.7 * np.eye(5)
    cov = FactorCovariance(factors=factors, mean=np.zeros(5), cov=np.outer(vols, vols) * corr,
                           n_obs=250, window="test", cov_id="toy")
    return ScenarioGenerator(cov)


def _linear_price(ms: MaterializedScenario) -> dict[str, float]:
    # Fake pricing boundary: $100M long NVDA, $50M short AMD, $200M long XLK, delta-one.
    v = ms.vector
    return {"T_NVDA": 100e6 * v.get("eq:NVDA", 0.0), "T_AMD": -50e6 * v.get("eq:AMD", 0.0),
            "T_XLK": 200e6 * v.get("eq:XLK", 0.0)}


def test_ablate_scenario_ladder_shapley_and_dedup():
    prov, _, _ = _provider()
    rec = ablate_scenario("ai_bust", "mild", provider=prov, generator=_toy_generator(),
                          price=_linear_price, tier_scale=1.0, state_mult=0.85)
    ladder = rec["ladder"]
    assert [s["layer"] for s in ladder] == ["authored", *LAYERS]
    total = ladder[-1]["total_pnl"]
    assert sum(s["marginal_pnl"] for s in ladder) == pytest.approx(total, rel=1e-12)
    # Shapley efficiency: the layers together explain full minus authored.
    assert sum(rec["shapley"].values()) == pytest.approx(total - ladder[0]["total_pnl"], rel=1e-9)
    assert rec["n_subsets"] == 2 ** len(LAYERS)
    # news/crowding/facts without xbrlrag_narrative compose to the base: priced once, not 2^6 times.
    assert rec["n_priced"] < rec["n_subsets"]
    assert rec["world_view_id"] == "macro-test-wv"
    assert rec["state_mult"] == 0.85


def test_floor_hits_reports_floored_equity_and_fx_only():
    assert floor_hits({"eq:NVDA": -0.95, "eq:AMD": -0.5, "fx:JPY": -0.95, "credit": -0.99}) == [
        "eq:NVDA", "fx:JPY"]


def test_ablate_scenario_records_floor_hits_per_subset():
    prov, _, _ = _provider(news_mult=1.5)
    rec = ablate_scenario("ai_bust", "severe", provider=prov, generator=_toy_generator(),
                          price=_linear_price, tier_scale=2.0, state_mult=1.0)
    full = next(s for s in rec["subsets"] if s["layers"] == sorted(LAYERS))
    assert "eq:NVDA" in full["floor_hits"]          # -0.43*2*1.5 = -1.29 before the -95% floor
    base = next(s for s in rec["subsets"] if s["layers"] == [])
    assert base["floor_hits"] == []


def test_cli_defaults_to_mild_and_rule_is_versioned():
    assert _parse_args([]).severity == "mild"
    assert COMPOSITION_RULE
