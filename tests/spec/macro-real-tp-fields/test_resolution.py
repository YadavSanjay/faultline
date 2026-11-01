"""macro-real-tp-fields C3-C4: the term-premium leg through ``MacroScenarioProvider.resolve`` (it is not an
IR node, so it never supersedes authored tenors), and the ``unmapped_fields`` record.

Expected values are hand-computed from the spec (docs/specs/macro-real-tp-fields.md, Requirements 2-3, C3-C4):
bp x 1e-4. rate_capital's authored drivers are the spec's (ir:DGS2 +100, ir:DGS10 +100, ir:DGS30 +150 bp),
written into a synthetic scenarios.json so the test does not depend on the committed sizing.
"""

from __future__ import annotations

import tempfile
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

RATE_CAPITAL_SPEC = {"ir:DGS2": {"move_bp": 100}, "ir:DGS10": {"move_bp": 100}, "ir:DGS30": {"move_bp": 150}}
RATE_CAPITAL = {"ir:DGS2": 0.0100, "ir:DGS10": 0.0100, "ir:DGS30": 0.0150}   # +100/+100/+150 bp x 1e-4


def _rate_capital(rt, resp: dict[str, Any]):
    """Resolve rate_capital (committed macro.json entry) over the spec's authored drivers."""
    with tempfile.TemporaryDirectory() as tmp:
        root = rt.synth_root(tmp, {"rate_capital": RATE_CAPITAL_SPEC})
        return rt.provider(rt.macro_cfg(), resp=resp, root=root).resolve("rate_capital")


def _synth(rt, resp: dict[str, Any] | None = None, *, exc: BaseException | None = None):
    """Resolve a synthetic scenario (authored eq:TSM -25%) whose macro entry asks nothing in particular."""
    with tempfile.TemporaryDirectory() as tmp:
        root = rt.synth_root(tmp, {"synth": {"eq:TSM": {"move_pct": -25}}})
        cfg = rt.macro_cfg(synth={"synthesis_question": "synthetic", "filters": {}})
        transport = rt.FakeMacroTransport(resp=resp, exc=exc)
        return rt.provider(cfg, transport=transport, root=root).resolve("synth")


def _unmapped(res) -> object:
    return res.to_dict().get("unmapped_fields")


# ----------------------------------------------------------------------------------------- C3


@pytest.mark.criterion("C3")
def test_c3_tp_only_adds_to_the_authored_curve_without_superseding(rt):
    # oracle: Requirement 2 + macro-curve-coherence Req. 3 by hand: tp:ACMTP10 is not ir:*, so the three
    # authored IR drivers stay and tp:ACMTP10 = 17 x 1e-4 = 0.0017 is added; nothing is superseded.
    res = _rate_capital(rt, rt.response({"TERM_PREMIUM_10Y": rt.field(17.0, "bp")}))
    assert res.used_macro is True
    assert res.drivers == pytest.approx({**RATE_CAPITAL, "tp:ACMTP10": 0.0017}, abs=1e-12)   # exact bp conv.
    assert res.to_dict()["superseded_static"] == []
    assert res.sources["tp:ACMTP10"]["origin"] == "macrorag"
    assert res.sources["tp:ACMTP10"]["macro_field"] == "TERM_PREMIUM_10Y"


@pytest.mark.criterion("C3")
def test_c3_ust10_plus_tp_supersedes_unsupplied_tenors(rt):
    # oracle: spec C3 by hand: UST_10Y +28 -> ir:DGS10 0.0028 (an IR leg -> authored DGS2, DGS30 superseded);
    # TERM_PREMIUM_10Y +17 -> tp:ACMTP10 0.0017.
    res = _rate_capital(rt, rt.response({"UST_10Y": rt.field(28.0, "bp"),
                                         "TERM_PREMIUM_10Y": rt.field(17.0, "bp")}))
    assert res.used_macro is True
    assert res.drivers == pytest.approx({"ir:DGS10": 0.0028, "tp:ACMTP10": 0.0017}, abs=1e-12)   # exact bp conv.
    assert res.to_dict()["superseded_static"] == ["ir:DGS2", "ir:DGS30"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("bad", ["undated", "missing"])
def test_c3_tp_citing_undated_or_missing_chunk_fails_the_gate(rt, bad):
    # oracle: Requirement 2 ("pass through MacroGate like any other driver"): the same TP leg citing a fresh
    # card applies (0.0017); citing an undated chunk or an id not in the pool fails the gate -> the
    # authored drivers exactly, fallback reason macro_gate_failed.
    good = _rate_capital(rt, rt.response({"TERM_PREMIUM_10Y": rt.field(17.0, "bp")}))
    assert good.drivers.get("tp:ACMTP10") == pytest.approx(0.0017, abs=1e-12)   # exact bp conversion
    chunks = [{"chunk_id": "f1", "release_date": rt.FRESH_DATE, "doc_type": "market_episode"},
              {"chunk_id": "u1", "release_date": "", "doc_type": "research_paper"}]
    cite = ["u1"] if bad == "undated" else ["nope"]
    res = _rate_capital(rt, rt.response({"TERM_PREMIUM_10Y": rt.field(17.0, "bp", cite)}, chunks))
    assert res.used_macro is False
    assert res.to_dict()["fallback_reason"] == "macro_gate_failed"
    assert res.drivers == pytest.approx(RATE_CAPITAL, abs=1e-12)   # authored, exact bp conversion


@pytest.mark.criterion("C3")
@given(tp=st.floats(min_value=-200.0, max_value=200.0, allow_nan=False),
       ry=st.one_of(st.none(), st.floats(min_value=-200.0, max_value=200.0, allow_nan=False)),
       ust10=st.one_of(st.none(), st.floats(min_value=-200.0, max_value=200.0, allow_nan=False)))
def test_c3_property_real_and_tp_legs_never_supersede(rt, tp, ry, ust10):
    # oracle: Requirement 2 as a set model: drivers = authored overridden by MacroRAG legs (bp x 1e-4); only
    # an ir:* MacroRAG leg (UST_10Y here) supersedes the authored ir:* tenors it did not supply.
    sv = {"TERM_PREMIUM_10Y": rt.field(tp, "bp")}
    macro = {"tp:ACMTP10": tp * 1e-4}
    if ry is not None:
        sv["REAL_YIELD_10Y"] = rt.field(ry, "bp")
        macro["real:DFII10"] = ry * 1e-4
    if ust10 is not None:
        sv["UST_10Y"] = rt.field(ust10, "bp")
        macro["ir:DGS10"] = ust10 * 1e-4
    superseded = ["ir:DGS2", "ir:DGS30"] if ust10 is not None else []
    expected = {k: v for k, v in RATE_CAPITAL.items() if k not in superseded} | macro
    res = _rate_capital(rt, rt.response(sv))
    assert res.used_macro is True
    assert res.drivers == pytest.approx(expected, rel=1e-12, abs=1e-18)   # float product rounding only
    assert res.to_dict()["superseded_static"] == superseded
    assert all(res.sources[k]["origin"] == "macrorag" for k in macro)


# ----------------------------------------------------------------------------------------- C4


@pytest.mark.criterion("C4")
def test_c4_unmapped_fields_listed_sorted(rt):
    # oracle: spec C4 example by hand: UST_10Y maps; XCCY_BASIS, EQUITY_US, MOVE_5Y do not -> sorted.
    res = _synth(rt, rt.response({"UST_10Y": rt.field(28.0), "XCCY_BASIS": rt.field(-5.0),
                                  "EQUITY_US": rt.field(-8.0, "pct"), "MOVE_5Y": rt.field(12.0, "pts")}))
    assert res.used_macro is True
    assert _unmapped(res) == ["EQUITY_US", "MOVE_5Y", "XCCY_BASIS"]


@pytest.mark.criterion("C4")
def test_c4_unused_hy_and_hard_dropped_fields_are_not_unmapped(rt):
    # oracle: Requirement 3 by hand: HY_OAS unused because IG_OAS is present is mapped -> []; a hard-dropped
    # XCCY_BASIS appears only in dropped_fields.
    both = _synth(rt, rt.response({"IG_OAS": rt.field(25.0), "HY_OAS": rt.field(300.0)}))
    assert _unmapped(both) == []
    dropped = _synth(rt, rt.response({"UST_10Y": rt.field(28.0),
                                      "XCCY_BASIS": rt.field(-5.0, validation=["implausible_magnitude"])}))
    assert _unmapped(dropped) == []
    assert dropped.to_dict()["dropped_fields"] == {"XCCY_BASIS": ["implausible_magnitude"]}


@pytest.mark.criterion("C4")
def test_c4_recorded_on_fallback_and_empty_without_response(rt):
    # oracle: Requirement 3 by hand: a response with only XCCY_BASIS leaves nothing to apply (fallback) yet
    # lists ["XCCY_BASIS"]; a failed synthesis call (no response) lists [].
    only_xccy = _synth(rt, rt.response({"XCCY_BASIS": rt.field(-5.0)}))
    assert only_xccy.used_macro is False
    assert _unmapped(only_xccy) == ["XCCY_BASIS"]
    failed = _synth(rt, exc=RuntimeError("synthetic transport failure"))
    assert failed.used_macro is False
    assert failed.to_dict()["fallback_reason"] == "macro_synthesis_failed"
    assert _unmapped(failed) == []


@pytest.mark.criterion("C4")
@given(subset=st.lists(st.sampled_from(["UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "UST", "IG_OAS", "HY_OAS",
                                        "MOVE", "MOVE_1Y", "MOVE_5Y", "MOVE_10Y", "MOVE_30Y", "VIX", "EQUITY_US",
                                        "USDTWD", "USDJPY", "XCCY_BASIS", "REAL_YIELD_10Y",
                                        "TERM_PREMIUM_10Y"]), unique=True))
def test_c4_property_unmapped_is_sorted_set_difference(rt, subset):
    # oracle: Requirement 3: for any subset S of the vocabulary, unmapped_fields = sorted(S - mapped set).
    res = _synth(rt, rt.response({n: rt.field(10.0) for n in subset}))
    assert _unmapped(res) == sorted(set(subset) - rt.MAPPED)
