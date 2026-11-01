"""Dual-RAG P1 merge tests: additive systematic (macro drivers) + idiosyncratic (ARag overlay)."""

from __future__ import annotations

import pytest
from src.scenariogen.arag.provider import AragResolution
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.macro.provider import DriverResolution

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
    def __init__(self, resolutions: dict[str, DriverResolution]):
        self._r = resolutions

    def resolve(self, name: str) -> DriverResolution:
        return self._r[name]


class FakeAragProvider:
    def __init__(self, resolutions: dict[str, AragResolution]):
        self._r = resolutions

    def resolve(self, name: str) -> AragResolution:
        return self._r[name]


def _macro_ai_bust() -> DriverResolution:
    return DriverResolution(
        scenario="ai_bust",
        drivers={"eq:NVDA": -0.35, "eq:XLK": -0.30, "credit": 0.009, "eqvol": 0.05},
        sources={"eq:NVDA": {"origin": "static"}, "eq:XLK": {"origin": "static"},
                 "credit": {"origin": "macrorag", "macro_field": "IG_OAS"},
                 "eqvol": {"origin": "macrorag", "macro_field": "VIX"}},
        used_macro=True, world_view_id="macro-2026-07-13")


def _arag_ai_bust() -> AragResolution:
    return AragResolution(
        scenario="ai_bust",
        company_drivers={"eq:NVDA": -0.3684, "eq:AMD": -0.36},
        sources={"eq:NVDA": {"origin": "arag", "ticker": "NVDA", "chunk_ids": ["c1"]},
                 "eq:AMD": {"origin": "arag", "ticker": "AMD", "chunk_ids": ["c2"]}},
        used_arag=True)


def _news_nvda(mult: float = 1.5):
    """A resolved NewsResolution with an applied gate and a single NVDA leg (P3/A3)."""
    from src.scenariogen.news.gate import NewsGateResult
    from src.scenariogen.news.mapping import NewsLeg
    from src.scenariogen.news.provider import NewsResolution

    gate = NewsGateResult(ok=True, newest_filing="2026-07-09", staleness_days=1,
                          n_names_with_events=3, reason="ok")
    leg = NewsLeg(ticker="NVDA", multiplier=mult, event_score=2.0, tone_z=-1.0, n_events=3,
                  accessions=["acc-1"], item_codes=["5.02"])
    return NewsResolution(as_of="2026-07-10", gate=gate, legs={"NVDA": leg},
                          live_triggers={"5.02": ["NVDA"]})


def test_news_scales_only_the_covered_idiosyncratic_leg():
    """P3/A3: news is a clamped multiplier on the ARag idiosyncratic leg (mode-(ii)). It scales the
    covered name (NVDA) and leaves an uncovered ARag name (AMD) — and the systematic legs — alone."""
    prov = DualRagEnrichmentProvider(
        scenario_config=AUTHORED,
        macro_provider=FakeMacroProvider({"ai_bust": _macro_ai_bust()}),
        arag_provider=FakeAragProvider({"ai_bust": _arag_ai_bust()}),
        news_resolution=_news_nvda(1.5))
    defn, dual = prov.definition_for("ai_bust", severity="severe", severity_scale=2.0)

    # NVDA overlay = idio(-0.3684) * severity(2.0) * news(1.5)
    assert defn.overlays["eq:NVDA"] == pytest.approx(-0.3684 * 2.0 * 1.5)
    assert defn.overlays["eq:AMD"] == pytest.approx(-0.36 * 2.0)          # no news leg -> unscaled
    assert dual.used_news
    assert dual.overlay_sources["eq:NVDA"]["calibration"]["news"]["multiplier"] == 1.5
    assert "calibration" not in dual.overlay_sources["eq:AMD"]
    assert dual.to_dict()["news"]["applied"] is True


def _crowding_nvda(mult: float = 1.2):
    """A resolved CrowdingResolution with a single NVDA leg (P7/F5)."""
    from src.scenariogen.crowding import CrowdingLeg, CrowdingResolution

    leg = CrowdingLeg(ticker="NVDA", multiplier=mult, days_to_cover=2.5, percentile=0.95,
                      is_short=False, squeeze=False)
    return CrowdingResolution(as_of="2026-07-10", legs={"NVDA": leg}, applied=True)


def test_news_and_crowding_compose_on_the_idiosyncratic_leg():
    """P7/F5: crowding multiplies the ARag idiosyncratic leg *and composes with* the news
    multiplier (idiosyncratic = ARag x news x crowding)."""
    prov = DualRagEnrichmentProvider(
        scenario_config=AUTHORED,
        macro_provider=FakeMacroProvider({"ai_bust": _macro_ai_bust()}),
        arag_provider=FakeAragProvider({"ai_bust": _arag_ai_bust()}),
        news_resolution=_news_nvda(1.5), crowding_resolution=_crowding_nvda(1.2))
    defn, dual = prov.definition_for("ai_bust", severity="severe", severity_scale=2.0)

    # NVDA overlay = idio(-0.3684) * severity(2.0) * news(1.5) * crowding(1.2)
    assert defn.overlays["eq:NVDA"] == pytest.approx(-0.3684 * 2.0 * 1.5 * 1.2)
    assert defn.overlays["eq:AMD"] == pytest.approx(-0.36 * 2.0)          # neither applies -> unscaled
    assert dual.used_news and dual.used_crowding
    cal = dual.overlay_sources["eq:NVDA"]["calibration"]
    assert cal["news"]["multiplier"] == 1.5 and cal["crowding"]["multiplier"] == 1.2


def test_news_none_leaves_the_merge_unchanged():
    prov = DualRagEnrichmentProvider(
        scenario_config=AUTHORED,
        macro_provider=FakeMacroProvider({"ai_bust": _macro_ai_bust()}),
        arag_provider=FakeAragProvider({"ai_bust": _arag_ai_bust()}),
        news_resolution=None)
    defn, dual = prov.definition_for("ai_bust", severity="severe", severity_scale=2.0)
    assert defn.overlays["eq:NVDA"] == pytest.approx(-0.3684 * 2.0)       # byte-identical to pre-P3
    assert not dual.used_news


def test_additive_merge_moves_company_leg_to_overlay():
    prov = DualRagEnrichmentProvider(
        scenario_config=AUTHORED,
        macro_provider=FakeMacroProvider({"ai_bust": _macro_ai_bust()}),
        arag_provider=FakeAragProvider({"ai_bust": _arag_ai_bust()}))
    defn, dual = prov.definition_for("ai_bust", severity="severe", severity_scale=2.0)

    # ARag-covered names leave the drivers (systematic move now comes from propagation) ...
    assert "eq:NVDA" not in defn.drivers
    assert "eq:XLK" in defn.drivers and "credit" in defn.drivers   # systematic legs stay
    # ... and enter the overlays, pre-scaled by the severity multiplier (idiosyncratic).
    assert abs(defn.overlays["eq:NVDA"] - (-0.3684 * 2.0)) < 1e-9
    assert abs(defn.overlays["eq:AMD"] - (-0.36 * 2.0)) < 1e-9

    assert dual.used_macro and dual.used_arag
    assert dual.overlay_sources["eq:NVDA"]["origin"] == "arag"
    assert abs(dual.overlay_sources["eq:NVDA"]["applied_move"] - (-0.7368)) < 1e-9
    assert dual.driver_sources["credit"]["origin"] == "macrorag"
    d = dual.to_dict()
    assert d["used_macro"] and d["used_arag"] and d["arag"]["scenario"] == "ai_bust"


def test_resolutions_cached_across_tiers():
    macro = FakeMacroProvider({"ai_bust": _macro_ai_bust()})
    arag = FakeAragProvider({"ai_bust": _arag_ai_bust()})
    calls = {"n": 0}
    orig = arag.resolve

    def counting(name):
        calls["n"] += 1
        return orig(name)

    arag.resolve = counting  # type: ignore[method-assign]
    prov = DualRagEnrichmentProvider(scenario_config=AUTHORED, macro_provider=macro, arag_provider=arag)
    for tier, scale in (("mild", 1.0), ("adverse", 1.5), ("severe", 2.0)):
        prov.definition_for("ai_bust", severity=tier, severity_scale=scale)
    assert calls["n"] == 1              # ARag resolved once, reused across 3 tiers


def test_idiosyncratic_only_falls_back_to_arag_driver():
    # keyman_nvda authored driver is eq:NVDA only; with no macro/systematic anchor and ARag
    # taking eq:NVDA, the additive split degenerates: the ARag shock becomes the DRIVER
    # (unscaled; materialize applies the tier), not a stopped scenario.
    arag = FakeAragProvider({"keyman_nvda": AragResolution(
        scenario="keyman_nvda", company_drivers={"eq:NVDA": -0.30},
        sources={"eq:NVDA": {"origin": "arag", "chunk_ids": ["c1"]}}, used_arag=True)})
    prov = DualRagEnrichmentProvider(scenario_config=AUTHORED, macro_provider=None, arag_provider=arag)
    defn, dual = prov.definition_for("keyman_nvda", severity="adverse", severity_scale=1.5)
    assert defn.drivers["eq:NVDA"] == -0.30          # ARag-as-driver (materialize scales later)
    assert "eq:NVDA" not in defn.overlays
    assert dual.driver_sources["eq:NVDA"]["origin"] == "arag"
    assert dual.driver_sources["eq:NVDA"]["mode"] == "driver"


def test_truly_empty_drivers_raises():
    # no authored drivers, no macro, no ARag legs -> genuine config error -> fail-loud.
    prov = DualRagEnrichmentProvider(scenario_config=AUTHORED, macro_provider=None, arag_provider=None)
    prov.static = {"scenarios": {"empty_scn": {"drivers": {}, "overlays": {}}}}
    with pytest.raises(ScenarioGenerationError) as ei:
        prov.definition_for("empty_scn", severity="adverse", severity_scale=1.5)
    assert "no drivers" in ei.value.reason


def test_no_providers_returns_authored_baseline():
    prov = DualRagEnrichmentProvider(scenario_config=AUTHORED, macro_provider=None, arag_provider=None)
    defn, dual = prov.definition_for("ai_bust", severity="adverse", severity_scale=1.0)
    assert defn.drivers["eq:NVDA"] == -0.35 and defn.drivers["eq:XLK"] == -0.30  # authored
    assert not dual.used_macro and not dual.used_arag
