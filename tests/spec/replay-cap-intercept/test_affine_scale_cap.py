"""replay-cap-intercept C1: the largest scale that keeps a vector affine in the scale inside its bounds.

Oracle: Requirement 1 by hand. For each bounded factor ``f``: ``c_f = at_zero.get(f, 0)``,
``s_f = at_one.get(f, 0) - c_f``; the raw cap is ``min_f (b_f - sign(s_f) * c_f) / |s_f|`` over bounded
factors with a non-zero slope (``math.inf`` with none), and the result is ``raw * (1 - margin)`` when
finite. Inputs are dyadic, so the hand values are exact up to the final ``(1 - margin)`` product.
"""

from __future__ import annotations

import copy
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.severity import affine_scale_cap, bound_scale_cap

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"
DEFAULT_MARGIN = 1e-9


@pytest.mark.criterion("C1")
def test_c1_point_intercepts_positive_side_binds():
    # oracle: hand calculation (bounds 1 unless stated):
    #   a: c  0.25,  at_one  0.75  -> s  0.5  -> (1 - 0.25) / 0.5   = 1.5   (outward on the + side, binds)
    #   b: c -0.125, at_one -0.625 -> s -0.5  -> (1 - 0.125) / 0.5  = 1.75
    #   c: c  0.5,   at_one  0.25  -> s -0.25 -> (1 + 0.5) / 0.25   = 6     (intercept on the inward side)
    #   d: c -0.25,  at_one  0.25  -> s  0.5  -> (1 + 0.25) / 0.5   = 2.5
    #   z: only in at_zero, c 0.5 -> s -0.5   -> (1 + 0.5) / 0.5    = 3
    #   m: bounded (0.25), in neither mapping -> c 0, s 0 -> no constraint
    #   u: unbounded (huge moves)               -> ignored
    #   raw = 1.5; default margin -> 1.5 * (1 - 1e-9); margin 0 -> 1.5; margin 0.5 -> 0.75.
    at_zero = {"a": 0.25, "b": -0.125, "c": 0.5, "d": -0.25, "z": 0.5, "u": 50.0}
    at_one = {"a": 0.75, "b": -0.625, "c": 0.25, "d": 0.25, "u": -80.0}
    bounds = {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0, "z": 1.0, "m": 0.25}
    # rel 1e-13: dyadic inputs are exact; 1e-13 still separates the 1e-9 margin from no margin
    assert affine_scale_cap(at_zero, at_one, bounds) == pytest.approx(1.5 * (1.0 - DEFAULT_MARGIN), rel=1e-13)
    assert affine_scale_cap(at_zero, at_one, bounds, margin=0.0) == pytest.approx(1.5, rel=1e-13)
    assert affine_scale_cap(at_zero, at_one, bounds, margin=0.5) == pytest.approx(0.75, rel=1e-13)


@pytest.mark.criterion("C1")
def test_c1_point_intercepts_negative_side_binds():
    # oracle: hand calculation (bounds 1):
    #   a: c  0.25, at_one  0.5  -> s  0.25 -> (1 - 0.25) / 0.25 = 3
    #   b: c -0.25, at_one -0.75 -> s -0.5  -> (1 - 0.25) / 0.5  = 1.5   (outward on the - side, binds)
    #   c: c  0.5,  missing from at_one -> s -0.5 -> (1 + 0.5) / 0.5 = 3
    #   raw = 1.5 (the proportional cap on at_one would be 1 / 0.75 = 1.333..., not this).
    #   Overshoot check of the spec's eq:CEG shape: c 0.25, at_one 0.5, b 0.75 -> (0.75 - 0.25) / 0.25 = 2.
    at_zero = {"a": 0.25, "b": -0.25, "c": 0.5}
    at_one = {"a": 0.5, "b": -0.75}
    bounds = {"a": 1.0, "b": 1.0, "c": 1.0}
    # rel 1e-13: dyadic inputs are exact; 1e-13 still separates the 1e-9 margin from no margin
    assert affine_scale_cap(at_zero, at_one, bounds, margin=0.0) == pytest.approx(1.5, rel=1e-13)
    assert affine_scale_cap(at_zero, at_one, bounds) == pytest.approx(1.5 * (1.0 - DEFAULT_MARGIN), rel=1e-13)
    assert affine_scale_cap({"x": 0.25}, {"x": 0.5}, {"x": 0.75}, margin=0.0) == pytest.approx(2.0, rel=1e-13)


@pytest.mark.criterion("C1")
def test_c1_point_intercept_on_bound_pushing_outward_caps_at_zero():
    # oracle: hand calculation: a: c 0.5, at_one 0.75 -> s 0.25, bound 2 -> (2 - 0.5) / 0.25 = 6;
    #   then add b: c 1 on its bound 1, at_one 1.5 -> s 0.5 -> (1 - 1) / 0.5 = 0 -> raw 0 -> 0 * (1 - m) = 0.
    # rel 1e-13: dyadic, exact; abs 0 for the exact zero
    assert affine_scale_cap({"a": 0.5}, {"a": 0.75}, {"a": 2.0}, margin=0.0) == pytest.approx(6.0, rel=1e-13)
    assert affine_scale_cap({"a": 0.5, "b": 1.0}, {"a": 0.75, "b": 1.5},
                            {"a": 2.0, "b": 1.0}) == pytest.approx(0.0, abs=0.0)


@pytest.mark.criterion("C1")
def test_c1_point_zero_intercept_equals_proportional_cap_with_margin():
    # oracle: Requirement 1 / C1: with at_zero all zeros the result is bound_scale_cap(at_one, bounds) * (1 - m);
    #   by hand: a: 0.5 vs 2 -> 4; b: -0.25 vs 1 -> 4; c: 0.125 vs 0.25 -> 2 -> raw 2.
    at_one = {"a": 0.5, "b": -0.25, "c": 0.125, "u": 9.0}
    bounds = {"a": 2.0, "b": 1.0, "c": 0.25}
    zeros = {"a": 0.0, "b": 0.0, "c": 0.0, "u": 0.0}
    for m in (0.0, DEFAULT_MARGIN, 0.25):
        expected = bound_scale_cap(at_one, bounds) * (1.0 - m)
        # rel 1e-13: same arithmetic on exact dyadic inputs
        assert affine_scale_cap(zeros, at_one, bounds, margin=m) == pytest.approx(expected, rel=1e-13)
        assert affine_scale_cap({}, at_one, bounds, margin=m) == pytest.approx(expected, rel=1e-13)
        assert expected == pytest.approx(2.0 * (1.0 - m), rel=1e-13)


@pytest.mark.criterion("C1")
def test_c1_point_every_bounded_slope_zero_is_inf():
    # oracle: Requirement 1: no bounded factor with a non-zero slope -> math.inf (whatever the margin);
    #   intercepts within (or on) their bound are allowed; unbounded factors may move.
    assert affine_scale_cap({"a": 0.5, "b": -1.0}, {"a": 0.5, "b": -1.0, "u": 3.0},
                            {"a": 1.0, "b": 1.0}) == math.inf
    assert affine_scale_cap({}, {"u": 1.0}, {"a": 1.0}, margin=0.5) == math.inf
    assert affine_scale_cap({"u": 2.0}, {"u": 7.0}, {}, margin=0.0) == math.inf


@pytest.mark.criterion("C1")
def test_c1_point_refusals():
    # oracle: Requirement 1: ValueError for margin outside [0, 1), a non-positive bound, or an intercept
    #   beyond its bound (|c_f| > b_f, also with zero slope and with the factor missing from at_one).
    at_zero, at_one, bounds = {"a": 0.25}, {"a": 0.75}, {"a": 1.0}  # valid: raw 1.5
    for bad in (-0.125, -1e-12, 1.0, 1.5, math.nan):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            affine_scale_cap(at_zero, at_one, bounds, margin=bad)
    for bad in (0.0, -1.0):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            affine_scale_cap(at_zero, at_one, {"a": 1.0, "b": bad})
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            affine_scale_cap(at_zero, {**at_one, "b": 0.5}, {"a": 1.0, "b": bad})
    for c in (1.25, -1.25):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            affine_scale_cap({**at_zero, "b": c}, {**at_one, "b": c}, {"a": 1.0, "b": 1.0})
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            affine_scale_cap({**at_zero, "b": c}, at_one, {"a": 1.0, "b": 1.0})


@pytest.mark.criterion("C1")
def test_c1_point_inputs_not_mutated():
    # oracle: Requirement 1: inputs are not mutated; the value is the hand cap (1 - 0.25) / 0.5 = 1.5,
    #   with the unbounded "u" and the missing-from-at_one "z" (c 0.5, s -0.5, b 2 -> 5) not binding.
    at_zero = {"a": 0.25, "z": 0.5, "u": 4.0}
    at_one = {"a": 0.75, "u": -4.0}
    bounds = {"a": 1.0, "z": 2.0, "m": 1.0}
    before = copy.deepcopy((at_zero, at_one, bounds))
    # rel 1e-13: dyadic, exact
    assert affine_scale_cap(at_zero, at_one, bounds, margin=0.0) == pytest.approx(1.5, rel=1e-13)
    assert (at_zero, at_one, bounds) == before


# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #
NAMES = ["f0", "f1", "f2", "f3", "f4"]
slope_st = st.integers(min_value=-64, max_value=64).map(lambda i: i / 16.0)
nonzero_slope = st.integers(min_value=1, max_value=64).flatmap(lambda i: st.sampled_from([i / 16.0, -i / 16.0]))
bound_st = st.integers(min_value=1, max_value=32).map(lambda i: i / 8.0)
frac_st = st.integers(min_value=-7, max_value=7)  # intercept = bound * frac / 8, so |c| < bound strictly
margin_pos = st.sampled_from([1e-9, 1e-6, 1e-3, 0.125, 0.5, 0.875])


@st.composite
def affine(draw, *, zero_intercept: bool = False):
    """(at_zero, at_one, bounds, c, s): dyadic, |c_f| < b_f, at least one bounded factor with s != 0.

    Some factors are left out of at_zero (c = 0) or out of at_one (at_one = 0); unbounded factors get
    arbitrary values.
    """
    n = draw(st.integers(min_value=1, max_value=len(NAMES)))
    names = NAMES[:n]
    bounded = [f for f in names if f == names[0] or draw(st.booleans())]
    bounds = {f: draw(bound_st) for f in bounded}
    c, s = {}, {}
    for f in names:
        b = bounds.get(f, 4.0)
        c[f] = 0.0 if zero_intercept else b * draw(frac_st) / 8.0
        s[f] = draw(nonzero_slope) if f == names[0] else draw(slope_st)
    at_zero, at_one = {}, {}
    for f in names:
        if abs(c[f]) > 0.0 or draw(st.booleans()):
            at_zero[f] = c[f]
        if abs(c[f] + s[f]) > 0.0 or draw(st.booleans()):
            at_one[f] = c[f] + s[f]
    return at_zero, at_one, bounds, c, s


def _hand_raw(c, s, bounds):
    ks = [(b - math.copysign(1.0, s[f]) * c[f]) / abs(s[f]) for f, b in bounds.items() if abs(s[f]) > 0.0]
    return min(ks) if ks else math.inf


@pytest.mark.criterion("C1")
@given(sc=affine(), m=margin_pos)
def test_c1_property_strictly_inside_with_positive_margin(sc, m):
    # oracle: property: k = hand raw * (1 - m); at k every bounded factor has |c + k s| < b strictly
    #   (|c| < b and m > 0, so v(k) is a strict convex mix of v(0) and v(raw)).
    at_zero, at_one, bounds, c, s = sc
    k = affine_scale_cap(at_zero, at_one, bounds, margin=m)
    raw = _hand_raw(c, s, bounds)
    # rel 1e-12: spec tolerance (one rounding in raw and one in the margin product)
    assert k == pytest.approx(raw * (1.0 - m), rel=1e-12)
    assert 0.0 < k < math.inf
    for f, b in bounds.items():
        assert abs(c[f] + k * s[f]) < b


@pytest.mark.criterion("C1")
@given(sc=affine())
def test_c1_property_zero_margin_lands_on_a_bound(sc):
    # oracle: property: with margin 0, k = hand raw; every bounded factor is within its bound and some bounded
    #   factor with a non-zero slope sits on it, both within 1e-12 relative.
    at_zero, at_one, bounds, c, s = sc
    k = affine_scale_cap(at_zero, at_one, bounds, margin=0.0)
    # rel 1e-12: spec tolerance
    assert k == pytest.approx(_hand_raw(c, s, bounds), rel=1e-12)
    rel_gap = {f: (b - abs(c[f] + k * s[f])) / b for f, b in bounds.items()}
    assert min(rel_gap.values()) >= -1e-12
    assert min(g for f, g in rel_gap.items() if abs(s[f]) > 0.0) <= 1e-12


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(sc=affine(), i=st.integers(min_value=0, max_value=14), j=st.integers(min_value=1, max_value=15))
def test_c1_property_cap_never_increases_with_margin(sc, i, j):
    # oracle: metamorphic: for margins m1 = i/16 <= m2 = min(i + j, 15)/16, cap(m2) <= cap(m1); each equals
    #   hand raw * (1 - m).
    at_zero, at_one, bounds, c, s = sc
    m1, m2 = i / 16.0, min(i + j, 15) / 16.0
    k1 = affine_scale_cap(at_zero, at_one, bounds, margin=m1)
    k2 = affine_scale_cap(at_zero, at_one, bounds, margin=m2)
    raw = _hand_raw(c, s, bounds)
    # rel 1e-12: spec tolerance
    assert k1 == pytest.approx(raw * (1.0 - m1), rel=1e-12)
    assert k2 == pytest.approx(raw * (1.0 - m2), rel=1e-12)
    assert k2 <= k1


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(sc=affine(zero_intercept=True), m=st.sampled_from([0.0, 1e-9, 0.25, 0.5]))
def test_c1_property_zero_intercept_matches_bound_scale_cap(sc, m):
    # oracle: C1: with at_zero all zeros (explicit or missing) the result equals
    #   bound_scale_cap(at_one, bounds) * (1 - m), and also the hand raw * (1 - m).
    at_zero, at_one, bounds, c, s = sc
    k = affine_scale_cap(at_zero, at_one, bounds, margin=m)
    # rel 1e-12: spec tolerance
    assert k == pytest.approx(bound_scale_cap(at_one, bounds) * (1.0 - m), rel=1e-12)
    assert k == pytest.approx(_hand_raw(c, s, bounds) * (1.0 - m), rel=1e-12)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(sc=affine(), z=st.integers(min_value=-800, max_value=800).map(lambda i: i / 8.0),
       o=st.integers(min_value=-800, max_value=800).map(lambda i: i / 8.0), m=margin_pos)
def test_c1_property_unbounded_factor_changes_nothing(sc, z, o, m):
    # oracle: metamorphic: adding an unbounded factor (any intercept, any value at 1) leaves the cap unchanged;
    #   the cap equals hand raw * (1 - m); the inputs are not mutated.
    at_zero, at_one, bounds, c, s = sc
    base = affine_scale_cap(at_zero, at_one, bounds, margin=m)
    z0, z1 = {**at_zero, "unbounded": z}, {**at_one, "unbounded": o}
    before = copy.deepcopy((z0, z1, bounds))
    got = affine_scale_cap(z0, z1, bounds, margin=m)
    # rel 1e-12: spec tolerance
    assert base == pytest.approx(_hand_raw(c, s, bounds) * (1.0 - m), rel=1e-12)
    assert got == pytest.approx(base, rel=1e-12)
    assert (z0, z1, bounds) == before
