"""dualrag-macro-gate C5-C7: field provenance carried into ``DriverResolution.sources``; fields with a hard
validation code dropped; the synthesis request carries the scenario horizon.

Expected values are hand-computed from the spec (docs/specs/dualrag-macro-gate.md, Requirements 3, 5, 6 and
the Notes' canonical conversion: bp x 1e-4, FX pct / 100, VIX pts / 100, MOVE pts / move_base=100) and the
committed configs/scenariogen/scenarios.json (static drivers and horizon_days).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.config import get_project_root
from src.scenariogen.macro.client import MacroRAGClient
from src.scenariogen.macro.provider import MacroScenarioProvider

HARD = ["implausible_magnitude", "unit_scale_mismatch", "horizon_mismatch"]   # spec Requirement 5
SOFT = ["horizon_unknown", "exceeds_observed_range"]                          # any other code -> kept

# Field pool: (shock value, unit, canonical targets, canonical move by hand from the Notes' conversion).
POOL = {
    "USDTWD": (5.0, "pct", ("fx:TWD",), 0.05),          # 5 pct / 100
    "USDJPY": (-3.0, "pct", ("fx:JPY",), -0.03),        # -3 pct / 100
    "VIX": (10.0, "pts", ("eqvol",), 0.10),             # 10 pts / 100
    "MOVE": (40.0, "pts", ("ratesvol",), 0.40),         # 40 pts / move_base 100
    "IG_OAS": (80.0, "bp", ("credit",), 0.008),         # 80 bp x 1e-4
    "UST": (-25.0, "bp", tuple(f"ir:{n}" for n in ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS3",
                                                     "DGS5", "DGS7", "DGS10", "DGS20", "DGS30")), -0.0025),
}
MODES = ["analog", "direct", None, "historical", ""]
# Committed scenarios.json horizon_days, read by hand (lines "horizon_days": 10 / 10 / 10 / 21).
COMMITTED_HORIZON = {"taiwan_geopolitical": 10, "ai_efficiency_shock": 10, "rate_capital": 10, "keyman_nvda": 21}


def _effective(mode):
    return "analog" if mode == "analog" else "direct"   # Requirement 3: anything else (or missing) -> direct


def _fresh(dm):
    return [dm.chunk("f1", dm.FRESH_DATE)]


def _root_with_horizon(tmp: str, scenario: str, horizon: int | None) -> Path:
    """A project root whose scenarios.json is the committed one with ``scenario``'s horizon_days set (or
    removed when None)."""
    src = get_project_root() / "configs" / "scenariogen" / "scenarios.json"
    dst_dir = Path(tmp) / "configs" / "scenariogen"
    dst_dir.mkdir(parents=True, exist_ok=True)
    doc = json.loads(src.read_text(encoding="utf-8"))
    if horizon is None:
        doc["scenarios"][scenario].pop("horizon_days", None)
    else:
        doc["scenarios"][scenario]["horizon_days"] = horizon
    (dst_dir / "scenarios.json").write_text(json.dumps(doc), encoding="utf-8")
    return Path(tmp)


def _posted_horizon(transport) -> object:
    assert transport.payloads, "no synthesis request was posted"
    _, body = transport.payloads[0]
    return (body.get("synthesis") or {}).get("horizon_days")


# ----------------------------------------------------------------------------------------- C5


@pytest.mark.criterion("C5")
def test_c5_sources_carry_mode_horizon_and_validation(dm):
    # oracle: Requirement 3 applied by hand: USDTWD analog, 10 days, no codes -> fx:TWD (analog, 10, []);
    # VIX direct, 5 days, soft code -> eqvol (direct, 5, ["horizon_unknown"]); MOVE "historical" with a
    # non-integer horizon -> ratesvol (direct, None, []); UST with nothing -> all 11 IR nodes (direct, None, []).
    resp = dm.response({"USDTWD": dm.field(5.0, "pct", ["f1"], "analog", horizon=10),
                        "VIX": dm.field(10.0, "pts", ["f1"], "direct", horizon=5, validation=["horizon_unknown"]),
                        "MOVE": dm.field(40.0, "pts", ["f1"], "historical", horizon="ten"),
                        "UST": dm.field(-25.0, "bp", ["f1"], None)},
                       _fresh(dm))
    expected = {"fx:TWD": ("analog", 10, []), "eqvol": ("direct", 5, ["horizon_unknown"]),
                "ratesvol": ("direct", None, [])}
    expected.update({f"ir:{n}": ("direct", None, []) for n in dm.IR_NODES})
    res = dm.macro_provider(resp=resp).resolve("taiwan_geopolitical")
    assert res.used_macro is True
    _, dual = dm.dual(dm.macro_provider(resp=resp), "taiwan_geopolitical", ["macro", "arag"]).definition_for(
        "taiwan_geopolitical")
    assert dual.macro is not None
    for sources in (res.sources, dual.macro.sources):
        for factor, (mode, horizon, codes) in expected.items():
            src = sources[factor]
            assert src.get("origin") == "macrorag"
            assert src.get("grounding_mode") == mode
            assert "source_horizon_days" in src and src["source_horizon_days"] == horizon
            assert "validation" in src and list(src["validation"]) == codes


@pytest.mark.criterion("C5")
def test_c5_parallel_ust_provenance_reaches_every_tenor(dm):
    # oracle: Requirement 3 + Notes: a parallel UST maps to every IR node, so an analog 10-day UST tags all
    # 11 ir: legs (analog, 10) and moves each by -25 bp x 1e-4 = -0.0025; the card is 587 days old but an
    # episode, so the gate passes (Requirement 4).
    resp = dm.response({"UST": dm.field(-25.0, "bp", ["k1"], "analog", horizon=10)},
                       [dm.chunk("k1", dm.STALE_DATE, dm.EPISODE)])
    res = dm.macro_provider(resp=resp).resolve("taiwan_geopolitical")
    assert res.used_macro is True
    for n in dm.IR_NODES:
        assert res.sources[f"ir:{n}"].get("grounding_mode") == "analog"
        assert res.sources[f"ir:{n}"].get("source_horizon_days") == 10
        assert res.drivers[f"ir:{n}"] == pytest.approx(-0.0025, rel=1e-12)  # exact bp conversion


@pytest.mark.criterion("C5")
@given(chosen=st.lists(st.sampled_from(sorted(POOL)), min_size=1, max_size=6, unique=True), data=st.data())
def test_c5_property_every_macro_leg_carries_its_fields_provenance(dm, chosen, data):
    # oracle: Requirement 3: each canonical leg's (grounding_mode, source_horizon_days, validation) equals its
    # field's (analog stays analog, else direct; an int horizon as-is, missing/non-integer -> None; codes as
    # sent, missing -> []). Soft codes only and fresh chunks, so every field is applied.
    shock, expected = {}, {}
    for name in chosen:
        value, unit, targets, _ = POOL[name]
        mode = data.draw(st.sampled_from(MODES), label=f"mode_{name}")
        horizon = data.draw(st.one_of(st.none(), st.integers(min_value=1, max_value=400), st.just("ten")),
                            label=f"horizon_{name}")
        codes = data.draw(st.one_of(st.none(), st.lists(st.sampled_from(SOFT), unique=True)), label=f"codes_{name}")
        shock[name] = dm.field(value, unit, ["f1"], mode, horizon=horizon, validation=codes)
        want = (_effective(mode), horizon if isinstance(horizon, int) else None, list(codes or []))
        expected.update(dict.fromkeys(targets, want))
    res = dm.macro_provider(resp=dm.response(shock, _fresh(dm))).resolve("taiwan_geopolitical")
    assert res.used_macro is True
    for factor, (mode, horizon, codes) in expected.items():
        src = res.sources[factor]
        assert (src.get("grounding_mode"), src.get("source_horizon_days"), list(src.get("validation", ["?"]))) == (
            mode, horizon, codes)
    assert {k for k, v in res.sources.items() if v.get("origin") == "macrorag"} == set(expected)


# ----------------------------------------------------------------------------------------- C6


@pytest.mark.criterion("C6")
def test_c6_hard_coded_fields_are_dropped_before_the_gate(dm):
    # oracle: Requirement 5 by hand on taiwan_geopolitical. VIX (implausible_magnitude) cites a stale paper
    # but is dropped -> no finding, no eqvol leg. USDTWD (unit_scale_mismatch) is dropped -> fx:TWD keeps its
    # static +8 pct = 0.08. UST_10Y with only a soft code is applied: -30 bp -> ir:DGS10 -0.0030. Static
    # eq:TSM -25 pct -> -0.25 stays.
    resp = dm.response({"VIX": dm.field(10.0, "pts", ["s1"], "direct", validation=["implausible_magnitude"]),
                        "USDTWD": dm.field(5.0, "pct", ["f1"], "direct", validation=["unit_scale_mismatch"]),
                        "UST_10Y": dm.field(-30.0, "bp", ["f1"], "direct", validation=["horizon_unknown"])},
                       [dm.chunk("s1", dm.STALE_DATE, "research_paper"), dm.chunk("f1", dm.FRESH_DATE)])
    gate = dm.gate_check(resp)
    assert gate.passed is True and gate.findings == []
    res = dm.macro_provider(resp=resp).resolve("taiwan_geopolitical")
    assert res.used_macro is True
    assert res.drivers == pytest.approx({"fx:TWD": 0.08, "eq:TSM": -0.25, "ir:DGS10": -0.0030},
                                        rel=1e-12)  # exact unit conversions
    assert "eqvol" not in res.drivers
    assert res.sources["fx:TWD"].get("origin") == "static"
    assert res.sources["ir:DGS10"].get("origin") == "macrorag"


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("code", HARD)
def test_c6_dropped_ig_lets_hy_map_to_credit(dm, code):
    # oracle: Requirement 5 ("treated as if absent") + mapping rule (HY_OAS is used for credit only when no
    # IG_OAS is present): IG_OAS 80 bp carrying a hard code is dropped, so HY_OAS 300 bp -> credit 0.03.
    # Contrast: the same IG_OAS with only a soft code wins -> credit 0.008.
    def resp(codes):
        return dm.response({"IG_OAS": dm.field(80.0, "bp", ["f1"], "direct", validation=codes),
                            "HY_OAS": dm.field(300.0, "bp", ["f1"], "direct")}, _fresh(dm))
    dropped = dm.macro_provider(resp=resp(["horizon_unknown", code])).resolve("ai_efficiency_shock")
    assert dropped.drivers["credit"] == pytest.approx(0.03, rel=1e-12)  # 300 bp x 1e-4
    assert dropped.sources["credit"].get("macro_field") == "HY_OAS"
    kept = dm.macro_provider(resp=resp(["horizon_unknown"])).resolve("ai_efficiency_shock")
    assert kept.drivers["credit"] == pytest.approx(0.008, rel=1e-12)    # 80 bp x 1e-4
    assert kept.sources["credit"].get("macro_field") == "IG_OAS"


@pytest.mark.criterion("C6")
@given(chosen=st.lists(st.sampled_from(sorted(POOL)), min_size=1, max_size=6, unique=True), data=st.data())
def test_c6_property_exactly_the_hard_coded_fields_are_dropped(dm, chosen, data):
    # oracle: Requirement 5: the applied macrorag legs are the targets of fields whose codes include no hard
    # code; each cites a stale paper when dropped (so a leak would also surface as a gate finding) and a
    # fresh chunk when kept. If nothing survives, plain mode falls back (used_macro false, Requirement 2).
    shock, kept = {}, {}
    for name in chosen:
        value, unit, targets, move = POOL[name]
        codes = data.draw(st.lists(st.sampled_from(HARD + SOFT), unique=True), label=f"codes_{name}")
        hard = any(c in HARD for c in codes)
        shock[name] = dm.field(value, unit, ["s1"] if hard else ["f1"], "direct", validation=codes)
        if not hard:
            kept.update(dict.fromkeys(targets, move))
    resp = dm.response(shock, [dm.chunk("s1", dm.STALE_DATE, "research_paper"), dm.chunk("f1", dm.FRESH_DATE)])
    assert dm.gate_check(resp).findings == []
    res = dm.macro_provider(resp=resp).resolve("taiwan_geopolitical")
    assert res.used_macro is bool(kept)
    assert {k for k, v in res.sources.items() if v.get("origin") == "macrorag"} == (set(kept) if kept else set())
    for factor, move in kept.items():
        assert res.drivers[factor] == pytest.approx(move, rel=1e-12)  # exact unit conversion


# ----------------------------------------------------------------------------------------- C7


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("scenario", sorted(COMMITTED_HORIZON))
def test_c7_request_carries_the_committed_scenario_horizon(dm, scenario):
    # oracle: Requirement 6 + committed scenarios.json horizon_days read by hand (10, 10, 10; keyman_nvda 5);
    # question and factor_tags are the committed macro.json entry, sent unchanged with mode "synthesize".
    cfg = dm.macro_cfg()["scenarios"][scenario]
    transport = dm.FakeMacroTransport(resp=dm.response({"VIX": dm.field(4.0, "pts", ["e1"], "direct", horizon=5)},
                                                       [dm.chunk("e1", dm.STALE_DATE, dm.EPISODE)]))
    dm.macro_provider(transport=transport).resolve(scenario)
    assert transport.posts == 1
    path, body = transport.payloads[0]
    assert path == "/query"
    assert body.get("mode") == "synthesize"
    assert body.get("question") == cfg["synthesis_question"]
    assert body.get("filters", {}).get("factor_tags") == cfg["filters"]["factor_tags"]
    assert _posted_horizon(transport) == COMMITTED_HORIZON[scenario]


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_c7_missing_horizon_defaults_to_ten_and_failures_still_send_it(dm):
    # oracle: Requirement 6: a scenario without horizon_days requests 10; the horizon is sent whatever the
    # response turns out to be (here an HTTP error), i.e. it belongs to the request, not to the result.
    with tempfile.TemporaryDirectory() as tmp:
        root = _root_with_horizon(tmp, "keyman_nvda", None)
        transport = dm.FakeMacroTransport(exc=ConnectionError("down"))
        MacroScenarioProvider(dm.macro_cfg(), as_of=dm.AS_OF, client=MacroRAGClient(transport),
                              project_root=root).resolve("keyman_nvda")
        assert _posted_horizon(transport) == 10


@pytest.mark.criterion("C7")
@given(scenario=st.sampled_from(sorted(COMMITTED_HORIZON)), horizon=st.integers(min_value=1, max_value=260))
def test_c7_property_request_horizon_follows_scenarios_json(dm, scenario, horizon):
    # oracle: Requirement 6: editing only scenarios.json horizon_days changes the requested horizon to match.
    with tempfile.TemporaryDirectory() as tmp:
        root = _root_with_horizon(tmp, scenario, horizon)
        transport = dm.FakeMacroTransport(resp=dm.response({}, []))
        MacroScenarioProvider(dm.macro_cfg(), as_of=dm.AS_OF, client=MacroRAGClient(transport),
                              project_root=root).resolve(scenario)
        assert _posted_horizon(transport) == horizon
