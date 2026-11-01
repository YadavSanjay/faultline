"""scenario-layer-ablation C1-C2: ``compose_definition`` builds drivers, overlays and severity scale.

Every expected value is Requirement 1 of docs/specs/scenario-layer-ablation.md applied by hand to
synthetic factor dicts built here (canonical factor keys, decimal moves). No services, no data/.

Shared hand example (used by the point tests):

    authored_drivers  = {eq:SMH: -0.20, eq:NVDA: -0.35}
    authored_overlays = {eq:NVDA: -0.05}               # overlay on the same factor as a company leg
    macro_drivers     = {eq:SMH: -0.20, eq:NVDA: -0.35, ir:DGS10: -0.005}
    seed_legs         = {eq:NVDA: -0.30, eq:AMD: -0.20}
    calibrated_legs   = {eq:NVDA: -0.36, eq:AMD: -0.25}
    news_mult         = {eq:NVDA: 1.2}                 # eq:AMD missing -> 1
    crowding_mult     = {eq:NVDA: 1.25, eq:AMD: 1.1}
    tier_scale = 0.5, state_mult = 1.5
"""

from __future__ import annotations

import copy
import math
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.ablation import compose_definition

LAYERS = ("macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state")
# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"
FULL = frozenset(LAYERS)

SMH, NVDA, AMD, DGS10 = "eq:SMH", "eq:NVDA", "eq:AMD", "ir:DGS10"


def _example() -> dict[str, Any]:
    return {
        "authored_drivers": {SMH: -0.20, NVDA: -0.35},
        "authored_overlays": {NVDA: -0.05},
        "macro_drivers": {SMH: -0.20, NVDA: -0.35, DGS10: -0.005},
        "seed_legs": {NVDA: -0.30, AMD: -0.20},
        "calibrated_legs": {NVDA: -0.36, AMD: -0.25},
        "news_mult": {NVDA: 1.2},
        "crowding_mult": {NVDA: 1.25, AMD: 1.1},
        "tier_scale": 0.5,
        "state_mult": 1.5,
    }


def _assert_dict_close(actual: dict[str, float], expected: dict[str, float]) -> None:
    assert set(actual) == set(expected)
    for key, value in expected.items():
        # rel 1e-12: products of three or four decimals, only the multiplication order may differ;
        # abs 1e-15 guards an overlay sum that cancels to ~0.
        assert actual[key] == pytest.approx(value, rel=1e-12, abs=1e-15), key


def _assert_result(result: Any, drivers: dict[str, float], overlays: dict[str, float], scale: float) -> None:
    got_drivers, got_overlays, got_scale = result
    _assert_dict_close(got_drivers, drivers)
    _assert_dict_close(got_overlays, overlays)
    # rel 1e-12: one multiplication of two decimals
    assert got_scale == pytest.approx(scale, rel=1e-12)


def _reference(
    *,
    layers: frozenset[str],
    authored_drivers: dict[str, float],
    authored_overlays: dict[str, float],
    macro_drivers: dict[str, float] | None,
    seed_legs: dict[str, float],
    calibrated_legs: dict[str, float],
    news_mult: dict[str, float],
    crowding_mult: dict[str, float],
    tier_scale: float,
    state_mult: float,
) -> tuple[dict[str, float], dict[str, float], float]:
    """Requirement 1, bullet by bullet."""
    severity_scale = tier_scale * (state_mult if "state" in layers else 1.0)
    drivers = dict(macro_drivers) if "macrorag" in layers and macro_drivers is not None else dict(authored_drivers)
    overlays = dict(authored_overlays)
    if "xbrlrag_narrative" in layers and seed_legs:
        idio = {f: (calibrated_legs[f] if "xbrlrag_facts" in layers else seed_legs[f]) for f in seed_legs}
        m = {
            f: (news_mult.get(f, 1.0) if "news" in layers else 1.0)
            * (crowding_mult.get(f, 1.0) if "crowding" in layers else 1.0)
            for f in seed_legs
        }
        remaining = {f: v for f, v in drivers.items() if f not in seed_legs}
        if remaining:
            drivers = remaining
            for f in seed_legs:
                overlays[f] = overlays.get(f, 0.0) + idio[f] * severity_scale * m[f]
        else:
            drivers = {f: idio[f] * m[f] for f in seed_legs}
    return drivers, overlays, severity_scale


# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_base_set():
    # oracle: Requirement 1 by hand: no layers -> authored drivers, authored overlays, scale = tier_scale 0.5.
    result = compose_definition(layers=frozenset(), **_example())
    _assert_result(result, {SMH: -0.20, NVDA: -0.35}, {NVDA: -0.05}, 0.5)


@pytest.mark.criterion("C1")
def test_c1_macrorag_only():
    # oracle: Requirement 1 by hand: macrorag -> drivers = macro_drivers; overlays unscaled; scale 0.5.
    result = compose_definition(layers=frozenset({"macrorag"}), **_example())
    _assert_result(result, {SMH: -0.20, NVDA: -0.35, DGS10: -0.005}, {NVDA: -0.05}, 0.5)


@pytest.mark.criterion("C1")
def test_c1_full_set_anchored_scenario():
    # oracle: Requirement 1 by hand. scale = 0.5 x 1.5 = 0.75. Drivers = macro minus legs {NVDA, AMD}
    # = {SMH -0.20, DGS10 -0.005}. NVDA: m = 1.2 x 1.25 = 1.5, overlay = -0.05 + (-0.36)(0.75)(1.5)
    # = -0.05 - 0.405 = -0.455 (authored overlay added, not replaced). AMD: news missing -> 1,
    # m = 1.1, overlay = 0 + (-0.25)(0.75)(1.1) = -0.20625.
    result = compose_definition(layers=FULL, **_example())
    _assert_result(result, {SMH: -0.20, DGS10: -0.005}, {NVDA: -0.455, AMD: -0.20625}, 0.75)


@pytest.mark.criterion("C1")
def test_c1_full_set_idiosyncratic_only_scenario():
    # oracle: Requirement 1 by hand (keyman-style): every driver is a company leg, so no anchor remains;
    # drivers = {NVDA: calibrated x m} = -0.36 x (1.2 x 1.25) = -0.54, unscaled; overlays stay empty;
    # scale = 0.5 x 1.5 = 0.75.
    inputs = _example()
    inputs.update(
        authored_drivers={NVDA: -0.35},
        authored_overlays={},
        macro_drivers={NVDA: -0.35},
        seed_legs={NVDA: -0.30},
        calibrated_legs={NVDA: -0.36},
    )
    result = compose_definition(layers=FULL, **inputs)
    _assert_result(result, {NVDA: -0.54}, {}, 0.75)


@pytest.mark.criterion("C1")
def test_c1_news_and_crowding_without_narrative_equal_base():
    # oracle: Requirement 1: news/crowding/xbrlrag_facts act on company legs only, which need
    # xbrlrag_narrative -> identical to the base set (hand values of test_c1_base_set).
    for layers in ({"news"}, {"crowding"}, {"news", "crowding"}, {"xbrlrag_facts", "news", "crowding"}):
        result = compose_definition(layers=frozenset(layers), **_example())
        _assert_result(result, {SMH: -0.20, NVDA: -0.35}, {NVDA: -0.05}, 0.5)


@pytest.mark.criterion("C1")
def test_c1_narrative_without_facts_uses_seed_moves():
    # oracle: Requirement 1 by hand: narrative only -> idio = seed; m = 1; scale 0.5; drivers = authored
    # minus legs = {SMH -0.20}; NVDA overlay = -0.05 + (-0.30)(0.5) = -0.20; AMD = (-0.20)(0.5) = -0.10.
    result = compose_definition(layers=frozenset({"xbrlrag_narrative"}), **_example())
    _assert_result(result, {SMH: -0.20}, {NVDA: -0.20, AMD: -0.10}, 0.5)


@pytest.mark.criterion("C1")
def test_c1_leg_missing_from_news_mult_gets_one():
    # oracle: Requirement 1 by hand: narrative + news, scale 0.5. NVDA m = 1.2: -0.05 + (-0.30)(0.5)(1.2)
    # = -0.23. AMD absent from news_mult -> m = 1: (-0.20)(0.5) = -0.10.
    result = compose_definition(layers=frozenset({"xbrlrag_narrative", "news"}), **_example())
    _assert_result(result, {SMH: -0.20}, {NVDA: -0.23, AMD: -0.10}, 0.5)


@pytest.mark.criterion("C1")
def test_c1_macro_none_with_macrorag_equals_base():
    # oracle: Requirement 1: macro_drivers None -> drivers start as authored_drivers (base hand values).
    inputs = _example()
    inputs["macro_drivers"] = None
    result = compose_definition(layers=frozenset({"macrorag"}), **inputs)
    _assert_result(result, {SMH: -0.20, NVDA: -0.35}, {NVDA: -0.05}, 0.5)


@pytest.mark.criterion("C1")
def test_c1_state_only_scales_severity():
    # oracle: Requirement 1 by hand: state -> scale = 0.5 x 1.5 = 0.75; drivers/overlays as base.
    result = compose_definition(layers=frozenset({"state"}), **_example())
    _assert_result(result, {SMH: -0.20, NVDA: -0.35}, {NVDA: -0.05}, 0.75)


@pytest.mark.criterion("C1")
def test_c1_inputs_not_mutated():
    # oracle: C1 text: inputs are not mutated -> every input dict equals its deep copy after the call.
    inputs = _example()
    before = copy.deepcopy(inputs)
    result = compose_definition(layers=FULL, **inputs)
    assert inputs == before
    _assert_result(result, {SMH: -0.20, DGS10: -0.005}, {NVDA: -0.455, AMD: -0.20625}, 0.75)


_KEYS = st.sampled_from(["eq:NVDA", "eq:AMD", "eq:SMH", "eq:TSM", "ir:DGS10", "ir:DGS2", "fx:EURUSD"])
_MOVE = st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False)
_MULT = st.floats(min_value=0.8, max_value=1.5, allow_nan=False, allow_infinity=False)


@st.composite
def _inputs(draw: st.DrawFn) -> dict[str, Any]:
    leg_keys = draw(st.lists(_KEYS, unique=True, max_size=3))
    return {
        "layers": frozenset(draw(st.sets(st.sampled_from(LAYERS)))),
        "authored_drivers": draw(st.dictionaries(_KEYS, _MOVE, min_size=1, max_size=4)),
        "authored_overlays": draw(st.dictionaries(_KEYS, _MOVE, max_size=3)),
        "macro_drivers": draw(st.none() | st.dictionaries(_KEYS, _MOVE, min_size=1, max_size=5)),
        "seed_legs": {k: draw(_MOVE) for k in leg_keys},
        "calibrated_legs": {k: draw(_MOVE) for k in leg_keys},
        "news_mult": {k: draw(_MULT) for k in leg_keys if draw(st.booleans())},
        "crowding_mult": {k: draw(_MULT) for k in leg_keys if draw(st.booleans())},
        "tier_scale": draw(st.floats(min_value=0.1, max_value=3.0)),
        "state_mult": draw(st.floats(min_value=0.85, max_value=1.4)),
    }


@pytest.mark.criterion("C1")
@given(inputs=_inputs())
def test_c1_property_matches_requirement(inputs: dict[str, Any]):
    # oracle: _reference above, a line-by-line restatement of Requirement 1; inputs left unmutated.
    before = copy.deepcopy(inputs)
    expected = _reference(**copy.deepcopy(inputs))
    result = compose_definition(**inputs)
    assert inputs == before
    _assert_result(result, *expected)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(inputs=_inputs())
def test_c1_metamorphic_leg_layers_inert_without_narrative(inputs: dict[str, Any]):
    # oracle: Requirement 1: without xbrlrag_narrative, adding/removing xbrlrag_facts, news and crowding
    # changes nothing.
    base_layers = inputs.pop("layers") - {"xbrlrag_narrative"}
    plain = compose_definition(layers=base_layers - {"xbrlrag_facts", "news", "crowding"}, **inputs)
    with_legs = compose_definition(layers=base_layers | {"xbrlrag_facts", "news", "crowding"}, **inputs)
    _assert_result(with_legs, *plain)


# ----------------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
def test_c2_unknown_layer_raises():
    # oracle: C2: a layer id not in Requirement 1's table -> ValueError (fail-loud convention).
    for bad in ("bogus", "MacroRAG", "news "):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            compose_definition(layers=frozenset({"macrorag", bad}), **_example())


@pytest.mark.criterion("C2")
def test_c2_no_drivers_raises():
    # oracle: C2: authored drivers empty, no layer adds any -> the result has no drivers -> ValueError.
    inputs = _example()
    inputs["authored_drivers"] = {}
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        compose_definition(layers=frozenset(), **inputs)


@pytest.mark.criterion("C2")
def test_c2_mismatched_leg_keys_raise():
    # oracle: C2: seed_legs and calibrated_legs with different keys -> ValueError.
    inputs = _example()
    inputs["calibrated_legs"] = {NVDA: -0.36}
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        compose_definition(layers=FULL, **inputs)
    inputs["calibrated_legs"] = {NVDA: -0.36, AMD: -0.25, "eq:TSM": -0.1}
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        compose_definition(layers=FULL, **inputs)


@pytest.mark.criterion("C2")
def test_c2_bad_scales_raise():
    # oracle: C2: non-finite or non-positive tier_scale or state_mult -> ValueError.
    for bad in (0.0, -0.5, math.nan, math.inf, -math.inf):
        for field in ("tier_scale", "state_mult"):
            inputs = _example()
            inputs[field] = bad
            with pytest.raises(ValueError, match=ANY_MESSAGE):
                compose_definition(layers=FULL, **inputs)


@pytest.mark.criterion("C2")
@given(bad=st.text(min_size=0, max_size=12).filter(lambda s: s not in LAYERS), layers=st.sets(st.sampled_from(LAYERS)))
def test_c2_property_any_unknown_layer_raises(bad: str, layers: set[str]):
    # oracle: C2: any id outside Requirement 1's table, alongside any valid ones, -> ValueError.
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        compose_definition(layers=frozenset(layers | {bad}), **_example())


@pytest.mark.criterion("C2")
@given(
    bad=st.floats(max_value=0.0) | st.just(math.nan) | st.just(math.inf),
    field=st.sampled_from(["tier_scale", "state_mult"]),
)
def test_c2_property_non_positive_or_non_finite_scale_raises(bad: float, field: str):
    # oracle: C2: tier_scale/state_mult must be finite and > 0, else ValueError.
    inputs = _example()
    inputs[field] = bad
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        compose_definition(layers=FULL, **inputs)
