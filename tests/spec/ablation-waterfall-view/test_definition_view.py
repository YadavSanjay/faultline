"""ablation-waterfall-view C5: ``report_views.definition_view`` resolves the drivers and horizon to show.

Oracle: Requirement 5 by hand: the first rule that applies wins -- "report" (the entry's provenance
drivers are non-empty: copied as stored, scaled), "version" (stored spec with drivers), "config" (raw
spec without a replay block), "unresolved" (a replay with nothing resolved: drivers {}, horizon None).
Spec drivers / overlays are converted like ``scenario_gen.to_canonical``: a number as is; else the first
of move_bp (x 1e-4), move_pct (/ 100), move_pts (/ 100), move_rel, move; no unit key -> ValueError.
"""

from __future__ import annotations

import copy
import math
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.report_views import definition_view

# Unit conversions are one multiplication or division of a hand literal: rel=1e-12 absorbs only the
# x * 1e-4 vs x / 1e4 rounding difference.
REL = 1e-12


def replay_raw() -> dict:
    """A scenarios.json replay entry: empty drivers and the placeholder horizon."""
    return {"replay": {"start": "2022-01-03", "end": "2022-10-12"}, "drivers": {}, "horizon_days": 10,
            "overlays": {"VOL_SPX": {"move_pts": 10}}, "narrative": "AI deflation replay"}


def report_entry() -> dict:
    return {"scenario": "ai_deflation", "severity": "severe",
            "materialized": {"provenance": {
                "scenario_version": "v-ai-1", "method": "conditional", "severity_scale": 1.5, "horizon_days": 221,
                "drivers": {"EQ_NVDA": -0.6, "IR_SOFR_5Y": 0.0125}, "overlays": {"VOL_SPX": 0.15},
                "replay": {"start": "2022-01-03", "end": "2022-10-12", "horizon_days": 221, "n_observed": 2}}}}


def stored_replay() -> dict:
    """The replay-resolved spec in the version store (drivers as {"move": v})."""
    return {"replay": {"start": "2022-01-03", "end": "2022-10-12"},
            "drivers": {"EQ_NVDA": {"move": -0.4}, "IR_SOFR_5Y": {"move": 0.0075}}, "horizon_days": 221,
            "overlays": {"VOL_SPX": {"move_pts": 10}}, "method": "conditional"}


@pytest.mark.criterion("C5")
def test_c5_point_report_entry_of_a_replay(h):
    # oracle: Requirement 5 by hand: provenance drivers non-empty -> "report": drivers and overlays as
    #   stored (already x 1.5, not converted again), horizon 221 and scale 1.5 from the provenance,
    #   scaled true, replay = the provenance's replay block copied; stored_spec is ignored.
    expected = {"source": "report", "drivers": {"EQ_NVDA": -0.6, "IR_SOFR_5Y": 0.0125},
                "overlays": {"VOL_SPX": 0.15}, "horizon_days": 221, "severity_scale": 1.5, "scaled": True,
                "replay": {"start": "2022-01-03", "end": "2022-10-12", "horizon_days": 221, "n_observed": 2}}
    h.assert_close(definition_view(replay_raw(), entry=report_entry(), stored_spec=stored_replay()), expected)


@pytest.mark.criterion("C5")
def test_c5_point_held_out_replay_with_stored_spec(h):
    # oracle: Requirement 5 by hand: no entry, stored spec with drivers -> "version": {"move": -0.4} ->
    #   -0.4, {"move": 0.0075} -> 0.0075; overlay {"move_pts": 10} -> 10 / 100 = 0.1; horizon 221 from the
    #   stored spec; scale None; scaled false; replay {start, end} of the stored spec.
    expected = {"source": "version", "drivers": {"EQ_NVDA": -0.4, "IR_SOFR_5Y": 0.0075},
                "overlays": {"VOL_SPX": 10 / 100}, "horizon_days": 221, "severity_scale": None, "scaled": False,
                "replay": {"start": "2022-01-03", "end": "2022-10-12"}}
    h.assert_close(definition_view(replay_raw(), stored_spec=stored_replay()), expected)


@pytest.mark.criterion("C5")
def test_c5_point_authored_scenario_from_config_converts_each_unit(h):
    # oracle: Requirement 5 by hand: no replay key -> "config". move_bp 50 -> 50e-4 = 0.005; move_pct -20
    #   -> -0.2; move_pts 15 -> 0.15; move_rel 0.3 -> 0.3; move -0.05 -> -0.05; bare -0.25 -> -0.25;
    #   {move_bp 10, move 99} -> move_bp comes first -> 0.001; overlay move_pts 5 -> 0.05. Horizon 10 from
    #   the raw spec (not a placeholder here), replay None.
    raw = {"drivers": {"IR_SOFR_5Y": {"move_bp": 50}, "EQ_SPX": {"move_pct": -20}, "VOL_SPX": {"move_pts": 15},
                       "CR_IG": {"move_rel": 0.3}, "FX_EURUSD": {"move": -0.05}, "EQ_NDX": -0.25,
                       "IR_UST_10Y": {"move_bp": 10, "move": 99}},
           "overlays": {"VOL_NDX": {"move_pts": 5}}, "horizon_days": 10, "method": "conditional"}
    expected = {"source": "config",
                "drivers": {"IR_SOFR_5Y": 50 * 1e-4, "EQ_SPX": -20 / 100, "VOL_SPX": 15 / 100, "CR_IG": 0.3,
                            "FX_EURUSD": -0.05, "EQ_NDX": -0.25, "IR_UST_10Y": 10 * 1e-4},
                "overlays": {"VOL_NDX": 5 / 100}, "horizon_days": 10, "severity_scale": None, "scaled": False,
                "replay": None}
    out = definition_view(raw)
    assert out["source"] == "config"
    for k, v in expected["drivers"].items():
        assert out["drivers"][k] == pytest.approx(v, rel=REL)
    h.assert_close(out, expected)


@pytest.mark.criterion("C5")
def test_c5_point_replay_with_nothing_resolved(h):
    # oracle: Requirement 5 by hand: a replay, no entry, no stored spec -> "unresolved": drivers {},
    #   overlays from the raw spec (move_pts 10 -> 0.1), horizon None (10 is a placeholder), replay window
    #   as strings: str(date(2022, 1, 3)) = "2022-01-03".
    raw = replay_raw()
    raw["replay"] = {"start": date(2022, 1, 3), "end": date(2022, 10, 12)}
    expected = {"source": "unresolved", "drivers": {}, "overlays": {"VOL_SPX": 0.1}, "horizon_days": None,
                "severity_scale": None, "scaled": False, "replay": {"start": "2022-01-03", "end": "2022-10-12"}}
    h.assert_close(definition_view(raw), expected)


@pytest.mark.criterion("C5")
def test_c5_point_empty_or_missing_provenance_drivers_fall_through(h):
    # oracle: Requirement 5 by hand: provenance drivers {} -> rule "report" does not apply -> "version"
    #   when the stored spec has drivers; an entry without "materialized" and a stored spec with empty
    #   drivers fall through to "unresolved" for a replay; an empty-driver entry of an authored scenario
    #   falls through to "config".
    empty_entry = report_entry()
    empty_entry["materialized"]["provenance"]["drivers"] = {}
    assert definition_view(replay_raw(), entry=empty_entry, stored_spec=stored_replay())["source"] == "version"
    bare_entry = {"scenario": "ai_deflation", "severity": "severe"}
    stored_empty = {**stored_replay(), "drivers": {}}
    out = definition_view(replay_raw(), entry=bare_entry, stored_spec=stored_empty)
    h.assert_close(out, {"source": "unresolved", "drivers": {}, "overlays": {"VOL_SPX": 0.1}, "horizon_days": None,
                         "severity_scale": None, "scaled": False,
                         "replay": {"start": "2022-01-03", "end": "2022-10-12"}})
    authored = {"drivers": {"EQ_SPX": {"move_pct": -10}}, "overlays": {}, "horizon_days": 5}
    h.assert_close(definition_view(authored, entry=empty_entry),
                   {"source": "config", "drivers": {"EQ_SPX": -0.1}, "overlays": {}, "horizon_days": 5,
                    "severity_scale": None, "scaled": False, "replay": None})


@pytest.mark.criterion("C5")
def test_c5_point_mapping_without_unit_key_raises():
    # oracle: Requirement 5 by hand: {"pct": -20} has none of move_bp / move_pct / move_pts / move_rel /
    #   move -> ValueError (a driver of a config spec, and an overlay of a stored spec).
    with pytest.raises(ValueError, match=r"\S"):
        definition_view({"drivers": {"EQ_SPX": {"pct": -20}}, "overlays": {}, "horizon_days": 10})
    bad_stored = {**stored_replay(), "overlays": {"VOL_SPX": {"points": 5}}}
    with pytest.raises(ValueError, match=r"\S"):
        definition_view(replay_raw(), stored_spec=bad_stored)


@pytest.mark.criterion("C5")
def test_c5_point_inputs_not_mutated(h):
    # oracle: Requirement 5 by hand: "The inputs are not mutated" -> raw, entry and stored spec equal deep
    #   copies taken before the calls; results are the hand values above.
    raw, entry, stored = replay_raw(), report_entry(), stored_replay()
    before = copy.deepcopy((raw, entry, stored))
    assert definition_view(raw, entry=entry, stored_spec=stored)["horizon_days"] == 221
    assert definition_view(raw, stored_spec=stored)["drivers"] == pytest.approx({"EQ_NVDA": -0.4, "IR_SOFR_5Y": 0.0075},
                                                                               rel=REL)
    assert (raw, entry, stored) == before


# -------------------------------------------------------------------------------------- property tests
ENTRY = st.sampled_from(["none", "bare", "empty", "full"])
STORED = st.sampled_from(["none", "empty", "full"])


def _entry(kind):
    if kind == "none":
        return None
    if kind == "bare":
        return {"scenario": "x", "severity": "severe"}
    e = report_entry()
    if kind == "empty":
        e["materialized"]["provenance"]["drivers"] = {}
    return e


def _stored(kind):
    if kind == "none":
        return None
    s = stored_replay()
    if kind == "empty":
        s["drivers"] = {}
    return s


def _raw(is_replay):
    return replay_raw() if is_replay else {"drivers": {"EQ_SPX": {"move_pct": -10}}, "overlays": {},
                                            "horizon_days": 5}


@pytest.mark.criterion("C5")
@given(is_replay=st.booleans(), ek=ENTRY, sk=STORED)
def test_c5_property_scaled_iff_report_and_unresolved_only_for_replays(is_replay, ek, sk):
    # oracle: Requirement 5 by hand: scaled is true only in the "report" rule; "unresolved" is reached only
    #   when the raw spec has a replay block; the rule is the first that applies (report > version >
    #   config > unresolved).
    out = definition_view(_raw(is_replay), entry=_entry(ek), stored_spec=_stored(sk))
    assert out["scaled"] is (out["source"] == "report")
    if out["source"] == "unresolved":
        assert is_replay
    expected = ("report" if ek == "full" else "version" if sk == "full" else
                "unresolved" if is_replay else "config")
    assert out["source"] == expected


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(is_replay=st.booleans(), sk=STORED)
def test_c5_property_adding_a_driven_entry_always_gives_report(is_replay, sk):
    # oracle: Requirement 5 by hand: rule "report" comes first, so adding an entry with non-empty
    #   provenance drivers turns any result into source "report" with the provenance's drivers, whatever
    #   stored_spec is.
    raw, stored = _raw(is_replay), _stored(sk)
    without = definition_view(raw, stored_spec=stored)
    with_entry = definition_view(raw, entry=report_entry(), stored_spec=stored)
    assert without["source"] != "report"
    assert with_entry["source"] == "report"
    assert with_entry["drivers"] == {"EQ_NVDA": -0.6, "IR_SOFR_5Y": 0.0125}


FACTORS = {"move_bp": 1e-4, "move_pct": 1 / 100, "move_pts": 1 / 100, "move_rel": 1.0, "move": 1.0}
FINITE = st.floats(min_value=-1e12, max_value=1e12, allow_nan=False, allow_infinity=False)


@pytest.mark.criterion("C5")
@given(unit=st.sampled_from(sorted(FACTORS)), x=FINITE, as_overlay=st.booleans())
def test_c5_property_unit_conversion_matches_hand_factor(unit, x, as_overlay):
    # oracle: Requirement 5 by hand: move_bp x 1e-4, move_pct / 100, move_pts / 100, move_rel and move as
    #   is, for drivers and overlays alike. rel=1e-12 (multiply-vs-divide rounding); abs=1e-300 covers
    #   results that underflow to subnormals.
    spec = {"drivers": {"K": {"move": 1.0}}, "overlays": {}, "horizon_days": 3}
    spec["overlays" if as_overlay else "drivers"]["K2"] = {unit: x}
    out = definition_view(spec)
    got = out["overlays" if as_overlay else "drivers"]["K2"]
    assert math.isfinite(got)
    assert got == pytest.approx(x * FACTORS[unit], rel=REL, abs=1e-300)
