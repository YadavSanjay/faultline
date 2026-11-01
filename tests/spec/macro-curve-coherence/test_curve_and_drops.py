"""macro-curve-coherence C2-C3: ``direction_mismatch`` is a hard code; a partly-supplied UST curve
supersedes the authored IR tenors MacroRAG did not supply.

Expected values are hand-computed from the spec (docs/specs/macro-curve-coherence.md, Requirements 2-3):
bp x 1e-4 for IR and credit, pct / 100 for FX and equity, VIX pts / 100. rate_capital's authored drivers
are read by hand from the committed scenarios.json (ir:DGS2 +50, ir:DGS10 +100, ir:DGS30 +150 bp).
"""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given
from hypothesis import strategies as st

# MacroRAG field -> (canonical factor, unit, value used in the tests, canonical move by hand).
MACRO = {
    "UST_2Y": ("ir:DGS2", "bp", 40.0, 0.0040),
    "UST_5Y": ("ir:DGS5", "bp", 35.0, 0.0035),
    "UST_10Y": ("ir:DGS10", "bp", 28.0, 0.0028),
    "UST_30Y": ("ir:DGS30", "bp", 32.0, 0.0032),
    "IG_OAS": ("credit", "bp", 25.0, 0.0025),
    "VIX": ("eqvol", "pts", 6.0, 0.06),
    "USDTWD": ("fx:TWD", "pct", 2.0, 0.02),
}
# Authored (scenarios.json) driver pool: factor -> (authored spec, canonical move by hand).
AUTHORED_IR = {f"ir:{n}": ({"move_bp": 100 + 10 * i}, (100 + 10 * i) * 1e-4)
               for i, n in enumerate(("DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS3", "DGS5", "DGS7",
                                      "DGS10", "DGS20", "DGS30"))}
AUTHORED_OTHER = {
    "eq:TSM": ({"move_pct": -25}, -0.25),
    "fx:TWD": ({"move_pct": 8}, 0.08),
    "credit": ({"move_bp": 50}, 0.005),
}
RATE_CAPITAL = {"ir:DGS2": 0.0050, "ir:DGS10": 0.0100, "ir:DGS30": 0.0150}   # +50/+100/+150 bp x 1e-4


def _superseded(res) -> object:
    return res.to_dict().get("superseded_static")


# ----------------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("codes", [["direction_mismatch"], ["horizon_unknown", "direction_mismatch"]])
def test_c2_direction_mismatch_is_dropped_and_hy_takes_credit(mc, codes):
    # oracle: Requirement 2 + the mapping rule (HY_OAS maps to credit only when IG_OAS is absent): IG_OAS
    # 25 bp carrying direction_mismatch is dropped -> HY_OAS 300 bp -> credit 0.03. It cites a stale paper,
    # so a leak would also show as a gate finding.
    resp = mc.response({"IG_OAS": mc.field(25.0, "bp", ["s1"], validation=codes),
                        "HY_OAS": mc.field(300.0, "bp")},
                       [{"chunk_id": "f1", "release_date": mc.FRESH_DATE, "doc_type": "market_episode"},
                        {"chunk_id": "s1", "release_date": mc.STALE_DATE, "doc_type": "research_paper"}])
    assert mc.gate_check(resp).findings == []
    res = mc.provider(resp=resp).resolve("ai_deflation")
    assert res.used_macro is True
    assert res.drivers["credit"] == pytest.approx(0.03, rel=1e-12)   # 300 bp x 1e-4
    assert res.sources["credit"].get("macro_field") == "HY_OAS"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_c2_removing_the_code_restores_the_field(mc):
    # oracle: Requirement 2: the same VIX field is dropped with direction_mismatch (eqvol absent; ai_deflation has
    # no authored eqvol driver) and applied without it (6 pts / 100 = 0.06).
    def resolve(codes):
        return mc.provider(resp=mc.response({"VIX": mc.field(6.0, "pts", validation=codes),
                                             "IG_OAS": mc.field(25.0, "bp")})).resolve("ai_deflation")
    dropped, kept = resolve(["direction_mismatch"]), resolve(["horizon_unknown"])
    assert "eqvol" not in dropped.drivers
    assert kept.drivers["eqvol"] == pytest.approx(0.06, rel=1e-12)   # exact pts conversion


# ----------------------------------------------------------------------------------------- C3


@pytest.mark.criterion("C3")
def test_c3_rate_capital_partial_curve_supersedes_the_unsupplied_tenor(mc):
    # oracle: spec C3 by hand: UST_10Y +28 -> ir:DGS10 0.0028; UST_30Y +32 -> ir:DGS30 0.0032; IG_OAS +25 ->
    # credit 0.0025; authored ir:DGS2 (+50) was not supplied -> removed and reported.
    resp = mc.response({"UST_10Y": mc.field(28.0, "bp"), "UST_30Y": mc.field(32.0, "bp"),
                        "IG_OAS": mc.field(25.0, "bp")})
    expected = {"ir:DGS10": 0.0028, "ir:DGS30": 0.0032, "credit": 0.0025}
    res = mc.provider(resp=resp).resolve("rate_capital")
    assert res.used_macro is True
    assert res.drivers == pytest.approx(expected, rel=1e-12)   # exact bp conversions
    assert "ir:DGS2" not in res.sources
    assert _superseded(res) == ["ir:DGS2"]
    prov = mc.dual(mc.provider(resp=resp), "rate_capital", ["macro"])
    for tier, scale in (("mild", 1.0), ("adverse", 1.5), ("severe", 2.0)):
        defn, dual = prov.definition_for("rate_capital", severity=tier, severity_scale=scale)
        assert defn.drivers == pytest.approx(expected, rel=1e-12)   # drivers are unscaled until materialize
        assert "ir:DGS2" not in dual.driver_sources


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_c3_no_macro_ir_leg_or_failed_gate_keeps_the_authored_curve(mc):
    # oracle: Requirement 3: with only IG_OAS returned, no MacroRAG leg is an IR node -> all three authored
    # IR drivers stay (+ credit 0.0025); with a failing gate (stale paper) nothing is applied -> the authored
    # drivers exactly. Both report superseded_static == [].
    only_ig = mc.provider(resp=mc.response({"IG_OAS": mc.field(25.0, "bp")})).resolve("rate_capital")
    assert only_ig.drivers == pytest.approx({**RATE_CAPITAL, "credit": 0.0025}, rel=1e-12)
    assert _superseded(only_ig) == []
    stale = mc.response({"UST_10Y": mc.field(28.0, "bp", ["s1"])},
                        [{"chunk_id": "s1", "release_date": mc.STALE_DATE, "doc_type": "research_paper"}])
    failed = mc.provider(resp=stale).resolve("rate_capital")
    assert failed.used_macro is False
    assert failed.drivers == pytest.approx(RATE_CAPITAL, rel=1e-12)
    assert _superseded(failed) == []


@pytest.mark.criterion("C3")
def test_c3_parallel_ust_supersedes_nothing_it_covers_every_node(mc):
    # oracle: Requirement 3 + Notes: a parallel UST maps to all 11 nodes, so every authored tenor is supplied
    # (replaced, not superseded): each node -25 bp x 1e-4 = -0.0025, superseded_static == [].
    res = mc.provider(resp=mc.response({"UST": mc.field(-25.0, "bp")})).resolve("rate_capital")
    assert res.drivers == pytest.approx({f"ir:{n}": -0.0025 for n in mc.IR_NODES}, rel=1e-12)
    assert _superseded(res) == []


@pytest.mark.criterion("C3")
@given(authored_ir=st.lists(st.sampled_from(sorted(AUTHORED_IR)), unique=True),
       authored_other=st.lists(st.sampled_from(sorted(AUTHORED_OTHER)), unique=True),
       macro_fields=st.lists(st.sampled_from(sorted(MACRO)), min_size=1, unique=True))
def test_c3_property_drivers_follow_the_coherence_rule(mc, authored_ir, authored_other, macro_fields):
    # oracle: Requirement 3 as a set model: drivers = authored (canonical, by hand) overridden by the MacroRAG
    # legs; if any MacroRAG leg is ir:*, authored ir:* legs not supplied are removed and reported sorted.
    authored = {f: AUTHORED_IR[f] for f in authored_ir} | {f: AUTHORED_OTHER[f] for f in authored_other}
    if not authored:
        authored = {"eq:TSM": AUTHORED_OTHER["eq:TSM"]}
    macro = {MACRO[n][0]: MACRO[n][3] for n in macro_fields}
    expected = {f: move for f, (_, move) in authored.items()} | macro
    macro_ir = {f for f in macro if f.startswith("ir:")}
    superseded = sorted(f for f in authored if f.startswith("ir:") and f not in macro) if macro_ir else []
    for f in superseded:
        expected.pop(f)
    resp = mc.response({n: mc.field(MACRO[n][2], MACRO[n][1]) for n in macro_fields})
    with tempfile.TemporaryDirectory() as tmp:
        root = mc.synth_root(tmp, {"synth": {f: spec for f, (spec, _) in authored.items()}})
        cfg = mc.macro_cfg(synth={"synthesis_question": "synthetic", "filters": {}})
        res = mc.provider(cfg, resp=resp, root=root).resolve("synth")
    assert res.used_macro is True
    assert res.drivers == pytest.approx(expected, rel=1e-12)   # exact unit conversions
    assert _superseded(res) == superseded
    assert set(res.sources) == set(expected)
