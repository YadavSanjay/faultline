"""scenario-stability C1: ``stability.spread`` summarises one quantity across repetitions.

Oracle: Requirement 1 by hand: statistics over the present (non-``None``) values only; ``mean`` =
fsum / k; ``std`` the sample standard deviation (``None`` for k < 2); ``rel_range`` = range / max |x|
(``None`` when every present value is 0.0); ``sign_flip`` when there is a value > 0 and a value < 0.
``ValueError`` for an empty input, a non-number (``bool`` included), NaN or infinity.
"""

from __future__ import annotations

import copy
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.stability import spread

# Dyadic values (multiples of 1/8 below 1000) are exact in binary, so sums and shifts by a dyadic
# constant are exact and only the division / square root round.
DYADIC = st.integers(min_value=-8000, max_value=8000).map(lambda i: i / 8)
VALUE = st.one_of(st.none(), DYADIC)
SERIES = st.lists(VALUE, min_size=1, max_size=8)
PRESENT_SERIES = st.lists(VALUE, min_size=1, max_size=8).filter(lambda v: any(x is not None for x in v))

# Metamorphic comparisons: the two sides round differently (a scaled sum, a reordered square sum),
# so relative 1e-9 / absolute 1e-9 covers rounding on values up to 1e4 and nothing more.
META_REL = 1e-9
META_ABS = 1e-9


def _present(v):
    return [x for x in v if x is not None]


@pytest.mark.criterion("C1")
def test_c1_point_two_negative_values_and_one_absent(h):
    # oracle: Requirement 1 by hand on [-0.10, -0.12, None]: k = 2; min -0.12, max -0.10, range 0.02;
    #   mean (-0.10 - 0.12) / 2 = -0.11; deviations +-0.01 -> std = sqrt(0.0002 / 1) ~ 0.0141421;
    #   max |x| = 0.12 -> rel_range 0.02 / 0.12 ~ 0.166667; no value > 0 -> sign_flip False.
    expected = h.entry([-0.10, -0.12, None], mn=-0.12, mx=-0.10, rng=0.02, mean=-0.11,
                       std=math.sqrt(0.0002), rel_range=0.02 / 0.12, sign_flip=False)
    h.assert_close(spread([-0.10, -0.12, None]), expected)


@pytest.mark.criterion("C1")
def test_c1_point_sign_flip_series(h):
    # oracle: Requirement 1 by hand on [2.0, -1.0, 5.0]: min -1, max 5, range 6; mean 6 / 3 = 2;
    #   deviations 0, -3, 3 -> std = sqrt(18 / 2) = 3; max |x| = 5 -> rel_range 6 / 5 = 1.2;
    #   2 > 0 and -1 < 0 -> sign_flip True.
    expected = h.entry([2.0, -1.0, 5.0], mn=-1.0, mx=5.0, rng=6.0, mean=2.0, std=3.0, rel_range=1.2,
                       sign_flip=True)
    h.assert_close(spread([2.0, -1.0, 5.0]), expected)


@pytest.mark.criterion("C1")
def test_c1_point_all_absent(h):
    # oracle: Requirement 1 by hand: k == 0 -> min/max/range/mean/std/rel_range None, sign_flip False;
    #   n = 2, n_present = 0, values [None, None].
    expected = h.entry([None, None], mn=None, mx=None, rng=None, mean=None, std=None, rel_range=None,
                       sign_flip=False)
    h.assert_close(spread([None, None]), expected)


@pytest.mark.criterion("C1")
def test_c1_point_one_present_non_zero_value(h):
    # oracle: Requirement 1 by hand: k == 1 -> min = max = mean = 3.0, range 0.0, std None,
    #   rel_range 0.0 / 3.0 = 0.0; no negative value -> sign_flip False.
    expected = h.entry([None, 3.0, None], mn=3.0, mx=3.0, rng=0.0, mean=3.0, std=None, rel_range=0.0,
                       sign_flip=False)
    h.assert_close(spread([None, 3.0, None]), expected)


@pytest.mark.criterion("C1")
def test_c1_point_one_present_zero_value(h):
    # oracle: Requirement 1 by hand: k == 1 with the value 0.0 -> rel_range None (max |x| = 0),
    #   range 0.0, std None, mean 0.0; 0 counts as neither sign -> sign_flip False.
    expected = h.entry([0.0], mn=0.0, mx=0.0, rng=0.0, mean=0.0, std=None, rel_range=None, sign_flip=False)
    h.assert_close(spread([0.0]), expected)


@pytest.mark.criterion("C1")
def test_c1_point_all_present_values_zero(h):
    # oracle: Requirement 1 by hand: [0.0, 0.0, None] -> k = 2, range 0.0, mean 0.0, std sqrt(0 / 1) = 0.0;
    #   max |x| == 0 -> rel_range None; no value > 0 or < 0 -> sign_flip False.
    expected = h.entry([0.0, 0.0, None], mn=0.0, mx=0.0, rng=0.0, mean=0.0, std=0.0, rel_range=None,
                       sign_flip=False)
    h.assert_close(spread([0.0, 0.0, None]), expected)


@pytest.mark.criterion("C1")
def test_c1_point_zero_and_positive_is_no_sign_flip(h):
    # oracle: Requirement 1 by hand on [0.0, 1.0]: exactly 0 counts as neither sign -> sign_flip False;
    #   range 1.0, mean 0.5, deviations +-0.5 -> std = sqrt(0.5 / 1); rel_range 1.0 / 1.0 = 1.0.
    expected = h.entry([0.0, 1.0], mn=0.0, mx=1.0, rng=1.0, mean=0.5, std=math.sqrt(0.5), rel_range=1.0,
                       sign_flip=False)
    h.assert_close(spread([0.0, 1.0]), expected)


@pytest.mark.criterion("C1")
def test_c1_point_ints_accepted_and_returned_as_floats(h):
    # oracle: Requirement 1 by hand on (1, 2, 4) (a tuple of ints): values [1.0, 2.0, 4.0] as floats;
    #   range 3, mean 7 / 3; deviations -4/3, -1/3, 5/3 -> squares 42/9, / 2 = 7/3 -> std sqrt(7/3);
    #   rel_range 3 / 4 = 0.75.
    out = spread((1, 2, 4))
    expected = h.entry([1.0, 2.0, 4.0], mn=1.0, mx=4.0, rng=3.0, mean=7.0 / 3.0, std=math.sqrt(7.0 / 3.0),
                       rel_range=0.75, sign_flip=False)
    h.assert_close(out, expected)
    assert isinstance(out["values"], list)
    assert all(type(x) is float for x in out["values"])
    assert type(out["min"]) is float and type(out["max"]) is float


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    "values",
    [
        [],                               # empty
        [1.0, math.nan],                  # NaN
        [math.inf, 1.0],                  # +inf
        [None, -math.inf],                # -inf
        [True, 1.0],                      # a bool is rejected
        [1.0, False],                     # a bool is rejected (False too)
        ["0.5", 1.0],                     # a string
    ],
)
def test_c1_point_invalid_input_raises(values):
    # oracle: Requirement 1 by hand: empty input, NaN, infinity, a bool and a non-number -> ValueError.
    with pytest.raises(ValueError, match=r"\S"):
        spread(values)


@pytest.mark.criterion("C1")
def test_c1_point_input_not_mutated(h):
    # oracle: Requirement 1 by hand: "The input is not mutated" -> the list equals its earlier copy and
    #   the result is still the hand value of [2.0, -1.0, 5.0] (see the sign-flip test).
    values = [2.0, -1.0, 5.0]
    before = copy.deepcopy(values)
    out = spread(values)
    assert values == before
    h.assert_close(out, h.entry([2.0, -1.0, 5.0], mn=-1.0, mx=5.0, rng=6.0, mean=2.0, std=3.0,
                                rel_range=1.2, sign_flip=True))


@pytest.mark.criterion("C1")
@given(values=SERIES)
def test_c1_property_bounds_and_counts(values):
    # oracle: Requirement 1 by hand: n = len, n_present = count of non-None, values kept in place as
    #   floats; when k >= 1: min <= mean <= max (approx: mean is fsum / k, rounded once), range >= 0,
    #   rel_range None or in [0, 2]; std None exactly when k < 2.
    out = spread(values)
    xs = _present(values)
    assert out["n"] == len(values)
    assert out["n_present"] == len(xs)
    assert out["values"] == [None if v is None else float(v) for v in values]
    if not xs:
        assert out["min"] is None and out["mean"] is None and out["range"] is None
        assert out["sign_flip"] is False
        return
    assert out["min"] == min(xs) and out["max"] == max(xs)
    assert out["min"] <= out["mean"] + 1e-9 and out["mean"] <= out["max"] + 1e-9  # tolerance: one rounding
    assert out["range"] >= 0.0
    assert out["rel_range"] is None or 0.0 <= out["rel_range"] <= 2.0
    assert (out["std"] is None) == (len(xs) < 2)
    assert out["sign_flip"] == (any(x > 0 for x in xs) and any(x < 0 for x in xs))


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(values=PRESENT_SERIES, c=DYADIC)
def test_c1_metamorphic_shift_leaves_range_and_std(values, c):
    # oracle: Requirement 1 by hand: max(x + c) - min(x + c) = max - min, and the deviations from the
    #   mean are unchanged by a shift, so range and std are unchanged (dyadic c keeps the shift exact).
    base = spread(values)
    shifted = spread([None if v is None else v + c for v in values])
    assert base["n_present"] >= 1
    assert shifted["range"] == pytest.approx(base["range"], rel=META_REL, abs=META_ABS)
    if base["std"] is None:
        assert shifted["std"] is None
    else:
        assert shifted["std"] == pytest.approx(base["std"], rel=META_REL, abs=META_ABS)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(values=PRESENT_SERIES, a=st.sampled_from([0.25, 0.5, 1.5, 2.0, 3.0, 10.0]))
def test_c1_metamorphic_positive_scale(values, a):
    # oracle: Requirement 1 by hand: for a > 0, min/max/range/mean/std scale by a; rel_range =
    #   (a range) / (a max |x|) is unchanged; signs are unchanged, so sign_flip is unchanged.
    base = spread(values)
    scaled = spread([None if v is None else v * a for v in values])
    assert base["n_present"] >= 1
    for key in ("min", "max", "range", "mean"):
        assert scaled[key] == pytest.approx(a * base[key], rel=META_REL, abs=META_ABS)
    if base["std"] is None:
        assert scaled["std"] is None
    else:
        assert scaled["std"] == pytest.approx(a * base["std"], rel=META_REL, abs=META_ABS)
    if base["rel_range"] is None:
        assert scaled["rel_range"] is None
    else:
        assert scaled["rel_range"] == pytest.approx(base["rel_range"], rel=META_REL, abs=META_ABS)
    assert scaled["sign_flip"] == base["sign_flip"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(values=PRESENT_SERIES)
def test_c1_metamorphic_negation(values):
    # oracle: Requirement 1 by hand: negating every value maps min -> -max and max -> -min (exact for
    #   floats); range, std, rel_range (max |x| unchanged) and sign_flip are unchanged.
    base = spread(values)
    neg = spread([None if v is None else -v for v in values])
    assert base["n_present"] >= 1
    assert neg["min"] == -base["max"] and neg["max"] == -base["min"]
    assert neg["range"] == pytest.approx(base["range"], rel=META_REL, abs=META_ABS)
    assert neg["mean"] == pytest.approx(-base["mean"], rel=META_REL, abs=META_ABS)
    for key in ("std", "rel_range"):
        if base[key] is None:
            assert neg[key] is None
        else:
            assert neg[key] == pytest.approx(base[key], rel=META_REL, abs=META_ABS)
    assert neg["sign_flip"] == base["sign_flip"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(data=st.data(), values=PRESENT_SERIES)
def test_c1_metamorphic_permuting_present_values_changes_only_values(data, values):
    # oracle: Requirement 1 by hand: every statistic is a function of the multiset of present values,
    #   so permuting them among the present slots changes only ``values`` (which follows the new order).
    xs = _present(values)
    perm = data.draw(st.permutations(xs))
    it = iter(perm)
    permuted = [None if v is None else next(it) for v in values]
    base = spread(values)
    out = spread(permuted)
    assert base["n_present"] >= 1
    assert out["values"] == [None if v is None else float(v) for v in permuted]
    for key in ("n", "n_present", "min", "max", "range", "sign_flip"):
        assert out[key] == base[key]
    for key in ("mean", "std", "rel_range"):
        if base[key] is None:
            assert out[key] is None
        else:
            assert out[key] == pytest.approx(base[key], rel=META_REL, abs=META_ABS)
