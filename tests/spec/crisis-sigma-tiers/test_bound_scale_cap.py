"""crisis-sigma-tiers C3: the largest tier scale that keeps every bounded factor inside its hard bound.

Oracle: Requirement 3 by hand: ``k = min_f (b_f - sign(m_f) * o_f) / |m_f|`` over factors with a bound and a
non-zero move (``math.inf`` with none). Values are dyadic, so the hand results are exact.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.severity import bound_scale_cap

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


@pytest.mark.criterion("C3")
def test_c3_point_overlays_signs_and_unbounded():
    # oracle: hand calculation:
    #   a: m 0.5,   o 0.5,   b 2 -> (2 - 0.5) / 0.5 = 3
    #   b: m -0.25, o 0.25,  b 1 -> (1 + 0.25) / 0.25 = 5   (k=5: -1.25 + 0.25 = -1, on the bound)
    #   c: m 0,     o 0.25,  b 0.5 -> no constraint
    #   d: m 1, unbounded (its overlay 10 is ignored) -> no constraint;  e: bounded, no move -> none
    #   -> k = 3. Without overlays: a 4, b 4 -> 4. With a's overlay -0.5 (helping): a 5, b 5 -> 5.
    moves = {"a": 0.5, "b": -0.25, "c": 0.0, "d": 1.0}
    bounds = {"a": 2.0, "b": 1.0, "c": 0.5, "e": 0.25}
    # rel 1e-12: spec tolerance (dyadic, exact)
    assert bound_scale_cap(moves, bounds, {"a": 0.5, "b": 0.25, "c": 0.25, "d": 10.0}) == pytest.approx(
        3.0, rel=1e-12)
    assert bound_scale_cap(moves, bounds) == pytest.approx(4.0, rel=1e-12)
    assert bound_scale_cap(moves, bounds, {"a": -0.5, "b": 0.25}) == pytest.approx(5.0, rel=1e-12)


@pytest.mark.criterion("C3")
def test_c3_point_overlay_on_bound_and_basis_point_units():
    # oracle: hand calculation:
    #   overlay exactly on its bound with the move pushing outward -> k = (1 - 1) / 0.5 = 0;
    #   the same overlay with the move pushing inward -> k = (1 + 1) / 0.5 = 4;
    #   rates in decimal: m 0.015625 (1-yr yield), bound 0.0625 -> k = 4; with o -0.03125 against m -0.015625:
    #   (0.0625 - 0.03125) / 0.015625 = 2.
    # rel 1e-12: spec tolerance (dyadic, exact); abs 0 for the exact zero
    assert bound_scale_cap({"a": 0.5}, {"a": 1.0}, {"a": 1.0}) == pytest.approx(0.0, abs=0.0)
    assert bound_scale_cap({"a": -0.5}, {"a": 1.0}, {"a": 1.0}) == pytest.approx(4.0, rel=1e-12)
    assert bound_scale_cap({"IR_UST_1Y": 0.015625}, {"IR_UST_1Y": 0.0625}) == pytest.approx(4.0, rel=1e-12)
    assert bound_scale_cap({"IR_UST_1Y": -0.015625}, {"IR_UST_1Y": 0.0625},
                           {"IR_UST_1Y": -0.03125}) == pytest.approx(2.0, rel=1e-12)


@pytest.mark.criterion("C3")
def test_c3_point_no_binding_constraint_and_refusals():
    # oracle: Requirement 3: no bounded factor with a non-zero move -> math.inf; an overlay beyond its
    #   bound or a non-positive bound raises ValueError (here alongside a constraint that would give k = 2).
    assert bound_scale_cap({"a": 0.0, "d": 1.0}, {"a": 1.0}) == math.inf
    assert bound_scale_cap({"d": 1.0}, {}) == math.inf
    assert bound_scale_cap({"a": 0.0}, {"a": 1.0}, {"a": 1.0}) == math.inf
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        bound_scale_cap({"a": 0.5, "b": 0.5}, {"a": 1.0, "b": 2.0}, {"b": 2.5})
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        bound_scale_cap({"a": 0.5, "b": 0.5}, {"a": 1.0, "b": 2.0}, {"b": -2.5})
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        bound_scale_cap({"a": 0.5}, {"a": 1.0, "c": 0.5}, {"c": 0.75})
    for bad in (0.0, -1.0):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            bound_scale_cap({"a": 0.5, "b": 0.5}, {"a": 1.0, "b": bad})
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            bound_scale_cap({"a": 0.5}, {"a": 1.0, "c": bad})


# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #
NAMES = ["f0", "f1", "f2", "f3", "f4"]
move_st = st.integers(min_value=-64, max_value=64).map(lambda i: i / 16.0)
nonzero_move = st.integers(min_value=1, max_value=64).flatmap(
    lambda i: st.sampled_from([i / 16.0, -i / 16.0]))
bound_st = st.integers(min_value=1, max_value=32).map(lambda i: i / 8.0)
frac_st = st.integers(min_value=-8, max_value=8)  # overlay = bound * frac / 8, so |overlay| <= bound


@st.composite
def scenario(draw, *, with_overlays: bool = True):
    n = draw(st.integers(min_value=1, max_value=len(NAMES)))
    names = NAMES[:n]
    moves = {f: draw(move_st) for f in names}
    moves[names[0]] = draw(nonzero_move)  # at least one binding constraint
    bounded = [f for f in names if f == names[0] or draw(st.booleans())]
    bounds = {f: draw(bound_st) for f in bounded}
    overlays = {f: bounds[f] * draw(frac_st) / 8.0 for f in bounded} if with_overlays else {}
    return moves, bounds, overlays


def _hand(moves, bounds, overlays):
    ks = [(b - math.copysign(1.0, moves[f]) * overlays.get(f, 0.0)) / abs(moves[f])
          for f, b in bounds.items() if abs(moves.get(f, 0.0)) > 0.0]
    return min(ks) if ks else math.inf


@pytest.mark.criterion("C3")
@given(sc=scenario())
def test_c3_property_within_bounds_and_one_binds(sc):
    # oracle: property: at the returned k every bounded factor is within its bound (abs 1e-12) and one sits
    #   on it; k also equals the hand formula.
    moves, bounds, overlays = sc
    k = bound_scale_cap(moves, bounds, overlays)
    # rel 1e-12: spec tolerance
    assert k == pytest.approx(_hand(moves, bounds, overlays), rel=1e-12)
    assert k >= 0.0
    slack = [b - abs(k * moves.get(f, 0.0) + overlays.get(f, 0.0)) for f, b in bounds.items()]
    # abs 1e-12: spec tolerance for "within" / "on" the bound
    assert min(slack) >= -1e-12
    assert min(s for f, s in zip(bounds, slack, strict=True) if abs(moves.get(f, 0.0)) > 0.0) <= 1e-12


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(sc=scenario(with_overlays=False), e=st.integers(min_value=-4, max_value=4))
def test_c3_property_scaling_moves_scales_cap_inversely(sc, e):
    # oracle: metamorphic: with no overlays, scaling every move by c = 2**e > 0 scales the cap by 1 / c;
    #   the base cap is finite and positive (positive bounds, a non-zero bounded move).
    moves, bounds, _ = sc
    c = 2.0**e
    base = bound_scale_cap(moves, bounds)
    assert 0.0 < base < math.inf
    # rel 1e-12: spec tolerance (power-of-two scaling is exact)
    assert bound_scale_cap({f: c * m for f, m in moves.items()}, bounds) == pytest.approx(base / c, rel=1e-12)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(sc=scenario(), m=move_st, o=st.integers(min_value=-1024, max_value=1024).map(lambda i: i / 8.0))
def test_c3_property_unbounded_factor_changes_nothing(sc, m, o):
    # oracle: metamorphic: adding a factor with no bound (any move, any overlay) leaves the cap unchanged;
    #   the cap equals the hand formula.
    moves, bounds, overlays = sc
    base = bound_scale_cap(moves, bounds, overlays)
    got = bound_scale_cap({**moves, "unbounded": m}, bounds, {**overlays, "unbounded": o})
    # rel 1e-12: spec tolerance
    assert base == pytest.approx(_hand(moves, bounds, overlays), rel=1e-12)
    assert got == pytest.approx(base, rel=1e-12)
