"""macro-real-tp-fields C1-C2: ``card_value_mismatch`` is a hard code (the set equals MacroRAG's), and
``REAL_YIELD_10Y`` / ``TERM_PREMIUM_10Y`` map to ``real:DFII10`` / ``tp:ACMTP10`` in decimal (bp x 1e-4).

Expected values are hand-computed from the spec (docs/specs/macro-real-tp-fields.md, Requirements 1-2) and
the committed fixture copy of MacroRAG's HARD_VALIDATION_CODES.
"""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.macro.mapping import to_canonical_drivers
from src.scenariogen.macro.models import HARD_VALIDATION_CODES, MacroShockField, MacroSynthesis

FIVE = {"implausible_magnitude", "unit_scale_mismatch", "horizon_mismatch", "direction_mismatch",
        "card_value_mismatch"}   # Requirement 1, transcribed by hand
SOFT = ["horizon_unknown", "low_card_coverage", "analog_only"]   # any code outside FIVE is soft (Req. 1)


def _sf(name: str, value: float, *, unit: str = "bp", chunk_ids: list[str] | None = None,
        rationale: str = "synthetic", grounding_mode: str = "analog", horizon: int | None = 10,
        validation: list[str] | None = None) -> MacroShockField:
    return MacroShockField(name=name, value=value, unit=unit, chunk_ids=list(chunk_ids or ["f1"]),
                           rationale=rationale, grounding_mode=grounding_mode, source_horizon_days=horizon,
                           validation=list(validation or []))


def _by_factor(drivers) -> dict[str, object]:
    return {d.factor: d for d in drivers}


# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_hard_code_set_equals_macrorag_fixture(rt):
    # oracle: MacroRAG src/synthesis/validate.py:125 at ac29d30 (committed fixture copy) and Requirement 1.
    assert rt.hard_codes_fixture() == FIVE
    assert set(HARD_VALIDATION_CODES) == FIVE


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("codes", [["card_value_mismatch"], ["horizon_unknown", "card_value_mismatch"]])
def test_c1_card_value_mismatch_field_is_dropped_and_yields_no_driver(rt, codes):
    # oracle: Requirement 1 by hand: VIX carrying card_value_mismatch is absent from fields, listed in
    # dropped with its codes, and produces no eqvol driver; the soft-only IG_OAS 25 bp is kept -> credit
    # 25 x 1e-4 = 0.0025.
    resp = rt.response({"VIX": rt.field(6.0, "pts", validation=codes),
                        "IG_OAS": rt.field(25.0, "bp", validation=["horizon_unknown"])})
    synth = MacroSynthesis.from_response(resp)
    assert [f.name for f in synth.fields] == ["IG_OAS"]
    assert synth.dropped == {"VIX": codes}
    with tempfile.TemporaryDirectory() as tmp:
        root = rt.synth_root(tmp, {"synth": {"eq:TSM": {"move_pct": -25}}})
        cfg = rt.macro_cfg(synth={"synthesis_question": "synthetic", "filters": {}})
        res = rt.provider(cfg, resp=resp, root=root).resolve("synth")
    assert res.used_macro is True
    assert "eqvol" not in res.drivers
    assert res.drivers["credit"] == pytest.approx(0.0025, abs=1e-12)   # exact bp conversion
    assert res.to_dict()["dropped_fields"] == {"VIX": codes}


@pytest.mark.criterion("C1")
@given(extra=st.lists(st.sampled_from(sorted(FIVE) + SOFT), unique=True),
       soft=st.lists(st.sampled_from(SOFT), unique=True))
def test_c1_property_any_hard_code_drops_soft_only_keeps(extra, soft):
    # oracle: Requirement 1 as a set rule: a field is dropped iff its codes meet FIVE. TERM_PREMIUM_10Y
    # always carries card_value_mismatch (plus any extra codes) -> dropped with exactly its codes; UST_10Y
    # carries soft codes only -> kept.
    codes = ["card_value_mismatch", *[c for c in extra if c != "card_value_mismatch"]]
    resp = {"shock_vector": {"TERM_PREMIUM_10Y": {"value": 17.0, "unit": "bp", "chunk_ids": ["f1"],
                                                  "validation": codes},
                             "UST_10Y": {"value": 28.0, "unit": "bp", "chunk_ids": ["f1"], "validation": soft}},
            "chunks": []}
    synth = MacroSynthesis.from_response(resp)
    assert [f.name for f in synth.fields] == ["UST_10Y"]
    assert synth.dropped == {"TERM_PREMIUM_10Y": codes}


# ----------------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
def test_c2_tp_and_real_yield_map_in_decimal_with_provenance():
    # oracle: MacroRAG unit table (bp) and factor_keys convention (decimal): +17 bp x 1e-4 = 0.0017 on
    # tp:ACMTP10, +23 bp x 1e-4 = 0.0023 on real:DFII10; provenance carried verbatim from the field.
    fields = [_sf("TERM_PREMIUM_10Y", 17.0, chunk_ids=["c1", "c2"], rationale="tp rebuild",
                  grounding_mode="analog", horizon=10, validation=["horizon_unknown"]),
              _sf("REAL_YIELD_10Y", 23.0, chunk_ids=["c3"], rationale="real-yield repricing",
                  grounding_mode="direct", horizon=20)]
    got = _by_factor(to_canonical_drivers(fields, 100.0))
    assert set(got) == {"tp:ACMTP10", "real:DFII10"}
    tp, ry = got["tp:ACMTP10"], got["real:DFII10"]
    assert tp.move == pytest.approx(0.0017, abs=1e-12)   # spec tolerance abs 1e-12
    assert ry.move == pytest.approx(0.0023, abs=1e-12)   # spec tolerance abs 1e-12
    assert (tp.macro_field, tp.chunk_ids, tp.rationale, tp.grounding_mode, tp.source_horizon_days,
            tp.validation) == ("TERM_PREMIUM_10Y", ["c1", "c2"], "tp rebuild", "analog", 10, ["horizon_unknown"])
    assert (ry.macro_field, ry.chunk_ids, ry.rationale, ry.grounding_mode, ry.source_horizon_days,
            ry.validation) == ("REAL_YIELD_10Y", ["c3"], "real-yield repricing", "direct", 20, [])


@pytest.mark.criterion("C2")
@given(v=st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
       k=st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False))
def test_c2_property_move_is_bp_times_1e4_and_linear(v, k):
    # oracle: bp x 1e-4 (Requirement 2) for any finite v; linearity: move(k v) = k move(v).
    def moves(x: float) -> dict[str, float]:
        drv = to_canonical_drivers([_sf("TERM_PREMIUM_10Y", x), _sf("REAL_YIELD_10Y", x)], 100.0)
        return {d.factor: d.move for d in drv}
    base, scaled = moves(v), moves(k * v)
    assert set(base) == {"tp:ACMTP10", "real:DFII10"}
    for key in ("tp:ACMTP10", "real:DFII10"):
        # floating-point product rounding only; abs floor for v near 0
        assert base[key] == pytest.approx(v * 1e-4, rel=1e-12, abs=1e-18)
        assert scaled[key] == pytest.approx(k * base[key], rel=1e-9, abs=1e-15)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(names=st.lists(st.sampled_from(["UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "UST", "IG_OAS", "HY_OAS",
                                       "MOVE", "VIX", "USDTWD", "USDJPY", "XCCY_BASIS", "EQUITY_US"]),
                      unique=True))
def test_c2_adding_the_new_fields_leaves_other_drivers_unchanged(names):
    # oracle: Requirement 2 ("every existing mapping is unchanged"): drivers from a vector without the two
    # new fields equal the drivers from the same vector plus them, minus the two new drivers (which must
    # appear: tp 17 x 1e-4, real 23 x 1e-4).
    base_fields = [_sf(n, 10.0 + i, unit="pts" if n in ("MOVE", "VIX") else "bp") for i, n in enumerate(names)]
    before = [(d.factor, d.move, d.macro_field) for d in to_canonical_drivers(base_fields, 100.0)]
    after_all = to_canonical_drivers([*base_fields, _sf("TERM_PREMIUM_10Y", 17.0), _sf("REAL_YIELD_10Y", 23.0)],
                                     100.0)
    new = {d.factor: d.move for d in after_all if d.macro_field in ("TERM_PREMIUM_10Y", "REAL_YIELD_10Y")}
    assert new == pytest.approx({"tp:ACMTP10": 0.0017, "real:DFII10": 0.0023}, abs=1e-12)   # spec abs 1e-12
    after = [(d.factor, d.move, d.macro_field) for d in after_all
             if d.macro_field not in ("TERM_PREMIUM_10Y", "REAL_YIELD_10Y")]
    assert after == before
