"""dualrag-macro-gate C1-C2: a required MacroRAG leg fails loud in the dual-RAG path; a non-required one
(and plain --macro mode) keeps the static fallback.

Expected values come from the spec (docs/specs/dualrag-macro-gate.md, Requirements 1-2) applied by hand to
synthetic MacroRAG responses built in conftest.py. Static drivers are the oracle the spec names for C2:
the scenario's scenarios.json drivers converted with ``to_canonical``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.config import get_project_root
from src.scenariogen.arag.client import AragClient
from src.scenariogen.arag.provider import AragCompanyProvider
from src.scenariogen.config import load_arag_config
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.macro.client import MacroRAGClient, MacroTransportError
from src.scenariogen.macro.provider import MacroScenarioProvider

SCENARIOS = ("taiwan_geopolitical", "ai_efficiency_shock", "rate_capital", "keyman_nvda")
# The scenarios with authored drivers: only these have a static fallback (the rest take their drivers from replay).
STATIC_SCENARIOS = ("taiwan_geopolitical", "rate_capital", "keyman_nvda")
TIER_NAMES = ("mild", "adverse", "severe")
# Defect case (spec C3): the four (driver, chunk) pairs that are stale when no chunk is a market_episode card.
KINDS = ["http_error", "timeout", "connection", "parse", "empty", "refused", "gate_direct", "gate_unmarked",
         "gate_analog", "hard_invalid", "unmapped"]   # conftest.FAILURE_KINDS (spec C1 failure table)
DEFECT_PAIRS = (("fx:TWD", "k1"), ("fx:TWD", "k2"), ("eqvol", "k3"), ("ir:DGS10", "k4"))


def _raises_on_every_tier(prov: DualRagEnrichmentProvider, name: str,
                          tiers: dict[str, float]) -> list[ScenarioGenerationError]:
    errs = []
    for tier, scale in tiers.items():
        with pytest.raises(ScenarioGenerationError) as ei:
            prov.definition_for(name, severity=tier, severity_scale=scale)
        errs.append(ei.value)
    return errs


# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("kind", KINDS)
def test_c1_required_macro_failure_stops_the_scenario_with_its_reason(dm, kind):
    # oracle: Requirement 1 reason table applied by hand to each synthetic failure in conftest.failure
    # (transport raises -> macro_synthesis_failed; non-numeric value -> parse failure -> macro_synthesis_failed;
    # empty shock_vector, refused, all fields hard-invalid or unmapped -> macro_unavailable; stale non-episode
    # citations -> macro_gate_failed whatever the grounding_mode).
    kw, reason = dm.failure(kind)
    transport = dm.FakeMacroTransport(**kw)
    prov = dm.dual(dm.macro_provider(transport=transport), "taiwan_geopolitical", ["macro", "arag"])
    errs = _raises_on_every_tier(prov, "taiwan_geopolitical", dm.TIERS)
    for err in errs:
        assert err.scenario == "taiwan_geopolitical"
        assert err.reason == reason
    # Requirement 1: the macro resolution is cached per scenario -> one synthesis request for all tiers.
    assert transport.posts == 1


@pytest.mark.criterion("C1")
def test_c1_gate_failure_detail_carries_every_finding(dm):
    # oracle: Requirement 1 (detail contains the gate's findings) + hand ages: every chunk is
    # 2026-10-06 - 2025-02-26 = 587 days old > 550, so each of the 4 (driver, chunk) pairs is stale.
    resp = dm.defect_response("direct")
    prov = dm.dual(dm.macro_provider(resp=resp), "taiwan_geopolitical", ["macro", "arag"])
    err = _raises_on_every_tier(prov, "taiwan_geopolitical", {"adverse": 1.5})[0]
    assert err.reason == "macro_gate_failed"
    for factor, chunk in DEFECT_PAIRS:
        assert factor in err.detail and chunk in err.detail
    assert str(dm.STALE_AGE_DAYS) in err.detail
    findings = dm.gate_check(resp).findings          # the gate's own finding strings (wiring oracle)
    assert len(findings) == 4
    for f in findings:
        assert f in err.detail


@pytest.mark.criterion("C1")
def test_c1_not_configured_when_no_macro_provider_or_no_macro_entry(dm):
    # oracle: Requirement 1, macro_not_configured: no macro provider, or macro.json lacks the scenario.
    no_provider = dm.dual(None, "taiwan_geopolitical", ["macro", "arag"])
    for err in _raises_on_every_tier(no_provider, "taiwan_geopolitical", dm.TIERS):
        assert (err.scenario, err.reason) == ("taiwan_geopolitical", "macro_not_configured")
    transport = dm.FakeMacroTransport(resp=dm.defect_response("analog"))
    no_entry = dm.dual(dm.macro_provider(transport=transport, drop_scenario="taiwan_geopolitical"),
                       "taiwan_geopolitical", ["macro", "arag"])
    for err in _raises_on_every_tier(no_entry, "taiwan_geopolitical", dm.TIERS):
        assert (err.scenario, err.reason) == ("taiwan_geopolitical", "macro_not_configured")
    assert transport.posts == 0                      # nothing to synthesize without a macro.json entry


@pytest.mark.criterion("C1")
def test_c1_committed_arag_config_rate_capital_requires_macro(dm):
    # oracle: spec Notes: committed arag.json has rate_capital required_services ["macro"]; Requirement 1:
    # a transport error is macro_synthesis_failed, raised on every committed severity tier.
    root = get_project_root()
    arag = AragCompanyProvider(load_arag_config(root), as_of=dm.AS_OF,
                               client=AragClient(dm.InertAragTransport()),
                               project_root=root)
    macro = MacroScenarioProvider(dm.macro_cfg(), as_of=dm.AS_OF,
                                  client=MacroRAGClient(dm.FakeMacroTransport(exc=MacroTransportError("HTTP 500"))),
                                  project_root=root)
    prov = DualRagEnrichmentProvider(macro_provider=macro, arag_provider=arag, project_root=root)
    for err in _raises_on_every_tier(prov, "rate_capital", dm.TIERS):
        assert (err.scenario, err.reason) == ("rate_capital", "macro_synthesis_failed")


@pytest.mark.criterion("C1")
@given(kind=st.sampled_from(KINDS),
       scenario=st.sampled_from(SCENARIOS),
       spelling=st.sampled_from(["macro", "MACRO", "Macro"]),
       with_arag=st.booleans(),
       tier=st.sampled_from(TIER_NAMES))
def test_c1_property_any_required_spelling_any_failure_any_tier_raises(dm, kind, scenario, spelling, with_arag, tier):
    # oracle: Requirement 1: "macro" in required_services is matched case-insensitively, and the reason is
    # fixed by the failure kind (hand table in conftest.failure) whatever the scenario, tier or other services.
    kw, reason = dm.failure(kind)
    required = [spelling, "arag"] if with_arag else [spelling]
    prov = dm.dual(dm.macro_provider(**kw), scenario, required)
    with pytest.raises(ScenarioGenerationError) as ei:
        prov.definition_for(scenario, severity=tier, severity_scale=dm.TIERS[tier])
    assert (ei.value.scenario, ei.value.reason) == (scenario, reason)


# ----------------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_c2_dropping_macro_from_required_services_restores_static_fallback(dm):
    # oracle: Requirement 2 (static drivers = scenarios.json drivers via to_canonical, used_macro false)
    # against Requirement 1 on the same failure: only the required_services entry differs.
    expected = dm.static_drivers("taiwan_geopolitical")
    for kind in dm.FAILURE_KINDS:
        kw, reason = dm.failure(kind)
        relaxed = dm.dual(dm.macro_provider(**kw), "taiwan_geopolitical", ["arag"])
        for tier, scale in dm.TIERS.items():
            defn, res = relaxed.definition_for("taiwan_geopolitical", severity=tier, severity_scale=scale)
            assert defn.drivers == pytest.approx(expected, rel=1e-12)  # same to_canonical arithmetic
            assert res.used_macro is False
        strict = dm.dual(dm.macro_provider(**kw), "taiwan_geopolitical", ["arag", "macro"])
        with pytest.raises(ScenarioGenerationError) as ei:
            strict.definition_for("taiwan_geopolitical")
        assert ei.value.reason == reason


@pytest.mark.criterion("C2")
def test_c2_no_arag_entry_falls_back_while_plain_macro_mode_never_raises(dm):
    # oracle: Requirement 2: no arag.json entry -> static drivers, used_macro false; plain MacroScenarioProvider
    # returns the static drivers for the same failure. Contrast (Requirement 1): the same failure with
    # "macro" required stops the scenario with macro_gate_failed.
    resp = dm.defect_response("direct")
    expected = dm.static_drivers("taiwan_geopolitical")
    defn, res = dm.dual(dm.macro_provider(resp=resp), "taiwan_geopolitical", None).definition_for(
        "taiwan_geopolitical", severity="severe", severity_scale=2.0)
    assert defn.drivers == pytest.approx(expected, rel=1e-12)  # same to_canonical arithmetic
    assert res.used_macro is False
    plain = dm.macro_provider(resp=resp)
    assert plain.resolve("taiwan_geopolitical").drivers == pytest.approx(expected, rel=1e-12)
    pdefn, pres = plain.definition_for("taiwan_geopolitical", severity="mild", severity_scale=1.0)
    assert pdefn.drivers == pytest.approx(expected, rel=1e-12)
    assert pres.used_macro is False
    with pytest.raises(ScenarioGenerationError) as ei:
        dm.dual(dm.macro_provider(resp=resp), "taiwan_geopolitical", ["macro"]).definition_for("taiwan_geopolitical")
    assert ei.value.reason == "macro_gate_failed"


@pytest.mark.criterion("C2")
@given(kind=st.sampled_from(KINDS),
       scenario=st.sampled_from(STATIC_SCENARIOS),
       relaxed=st.sampled_from([[], ["arag"], ["ARAG"], None]),
       tier=st.sampled_from(TIER_NAMES))
def test_c2_property_non_required_and_plain_mode_return_static(dm, kind, scenario, relaxed, tier):
    # oracle: Requirement 2 for every failure x non-required config x tier (static drivers via to_canonical,
    # used_macro false, plain resolve/definition_for never raise); Requirement 1 for the required twin.
    kw, reason = dm.failure(kind)
    expected = dm.static_drivers(scenario)
    defn, res = dm.dual(dm.macro_provider(**kw), scenario, relaxed).definition_for(
        scenario, severity=tier, severity_scale=dm.TIERS[tier])
    assert defn.drivers == pytest.approx(expected, rel=1e-12)  # same to_canonical arithmetic
    assert res.used_macro is False
    plain = dm.macro_provider(**kw)
    assert plain.resolve(scenario).used_macro is False
    assert plain.definition_for(scenario, severity=tier)[0].drivers == pytest.approx(expected, rel=1e-12)
    for unconfigured in (dm.macro_provider(no_client=True), dm.macro_provider(drop_scenario=scenario, **kw)):
        assert unconfigured.resolve(scenario).drivers == pytest.approx(expected, rel=1e-12)
    with pytest.raises(ScenarioGenerationError) as ei:
        dm.dual(dm.macro_provider(**kw), scenario, ["macro"]).definition_for(scenario, severity=tier)
    assert ei.value.reason == reason
