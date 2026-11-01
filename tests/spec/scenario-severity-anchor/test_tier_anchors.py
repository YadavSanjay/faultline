"""scenario-severity-anchor C2: mild / adverse / severe anchors from a factor's horizon moves.

Oracle: Requirement 2 by hand: ``p = h / (252 * years)``, ``k = max(1, ceil(n * p))``; mild is the ``k``-th
smallest horizon move for ``"down"`` and the ``k``-th largest for ``"up"``; adverse is the record; severe is
``adverse * severe_factor`` with its magnitude capped at ``bound``. Moves are dyadic, so order statistics
and products are exact.
"""

from __future__ import annotations

import math
from fractions import Fraction

import pandas as pd
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.scenariogen.severity import tier_anchors

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"
KEYS = {"mild", "adverse", "severe"}

# 20 distinct dyadic daily moves. Sorted: -0.5, -0.375, -0.25, -0.1875, ... , 0.375, 0.5, 0.75.
TWENTY = [
    0.0625, -0.125, 0.03125, -0.5, 0.25, -0.0625, 0.015625, -0.25, 0.125, -0.03125,
    0.375, -0.375, 0.0078125, -0.015625, 0.1875, -0.1875, 0.5, -0.0078125, 0.75, 0.0,
]  # fmt: skip


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.bdate_range("1999-01-04", periods=len(vals)))


def _check(got: dict[str, float], mild: float, adverse: float, severe: float) -> None:
    assert set(got) == KEYS
    # rel 1e-12: spec tolerance (dyadic values, exact)
    assert got["mild"] == pytest.approx(mild, rel=1e-12)
    assert got["adverse"] == pytest.approx(adverse, rel=1e-12)
    assert got["severe"] == pytest.approx(severe, rel=1e-12)


@pytest.mark.criterion("C2")
def test_c2_point_k_above_one():
    # oracle: Requirement 2 by hand: h=1 so the 20 moves are the windows (n=20); years=1/32 ->
    # p = 1 / 7.875, n*p = 2.54 -> k = 3. Down: 3rd smallest -0.25, record -0.5, severe -0.5*1.5 = -0.75.
    # Up: 3rd largest 0.375, record 0.75, severe 1.125.
    assert max(1, math.ceil(20 * 1 / (252 * 0.03125))) == 3
    s = _series(TWENTY)
    down = tier_anchors(s, "credit", 1, "down", mild_return_years=0.03125)
    _check(down, -0.25, -0.5, -0.75)
    up = tier_anchors(s, "credit", 1, "up", mild_return_years=0.03125)
    _check(up, 0.375, 0.75, 1.125)
    # severe_factor 2: -1.0 and 1.5
    _check(tier_anchors(s, "credit", 1, "down", mild_return_years=0.03125, severe_factor=2.0), -0.25, -0.5, -1.0)
    _check(tier_anchors(s, "credit", 1, "up", mild_return_years=0.03125, severe_factor=2.0), 0.375, 0.75, 1.5)


@pytest.mark.criterion("C2")
def test_c2_point_k_clamped_to_one():
    # oracle: Requirement 2 by hand: default years 10, h=1, n=20 -> n*p = 20/2520 -> k = max(1, 1) = 1,
    # so mild is the record: down -0.5 / -0.5 / -0.75, up 0.75 / 0.75 / 1.125.
    s = _series(TWENTY)
    _check(tier_anchors(s, "ir:DGS10", 1, "down"), -0.5, -0.5, -0.75)
    _check(tier_anchors(s, "ir:DGS10", 1, "up"), 0.75, 0.75, 1.125)


@pytest.mark.criterion("C2")
def test_c2_point_compounded_windows():
    # oracle: Requirement 2 by hand on Requirement 1's windows: eq returns 0.5, -0.5, 0.25, -0.25, 0.0, h=2 ->
    # windows -0.25, -0.375, -0.0625, -0.25 (n=4, k=1). Down: -0.375 / -0.375 / -0.5625.
    # Up: the largest is -0.0625, severe -0.09375.
    s = _series([0.5, -0.5, 0.25, -0.25, 0.0])
    _check(tier_anchors(s, "eq:NVDA", 2, "down"), -0.375, -0.375, -0.5625)
    _check(tier_anchors(s, "eq:NVDA", 2, "up"), -0.0625, -0.0625, -0.09375)


@pytest.mark.criterion("C2")
def test_c2_point_bound_caps_severe_keeping_sign():
    # oracle: Requirement 2 by hand: bound 0.625 caps |-0.75| and |1.125| at 0.625 with their signs;
    # bound 1.0 leaves -0.75 alone and caps 1.125 at 1.0; bound 2.0 changes nothing.
    s = _series(TWENTY)
    y = 0.03125
    _check(tier_anchors(s, "credit", 1, "down", mild_return_years=y, bound=0.625), -0.25, -0.5, -0.625)
    _check(tier_anchors(s, "credit", 1, "up", mild_return_years=y, bound=0.625), 0.375, 0.75, 0.625)
    _check(tier_anchors(s, "credit", 1, "down", mild_return_years=y, bound=1.0), -0.25, -0.5, -0.75)
    _check(tier_anchors(s, "credit", 1, "up", mild_return_years=y, bound=1.0), 0.375, 0.75, 1.0)
    _check(tier_anchors(s, "credit", 1, "up", mild_return_years=y, bound=2.0), 0.375, 0.75, 1.125)


@pytest.mark.criterion("C2")
def test_c2_refusals():
    # oracle: Requirement 2: a bad direction, mild_return_years <= 0, severe_factor < 1 or bound <= 0 raises.
    s = _series(TWENTY)
    for direction in ("Down", "sideways", ""):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            tier_anchors(s, "credit", 1, direction)
    for years in (0.0, -1.0):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            tier_anchors(s, "credit", 1, "down", mild_return_years=years)
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        tier_anchors(s, "credit", 1, "down", severe_factor=0.99)
    for bound in (0.0, -0.5):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            tier_anchors(s, "credit", 1, "up", bound=bound)


_RET = st.integers(min_value=-15, max_value=16).map(lambda i: i / 16)
_YEARS = st.sampled_from([0.015625, 0.03125, 0.0625, 0.25, 1.0, 10.0])
_SEVERE = st.sampled_from([1.0, 1.25, 1.5, 2.0])
_SUM_FACTOR = st.sampled_from(["ir:DGS2", "credit", "eqvol"])


def _windows(vals: list[float], factor: str, h: int) -> list[float]:
    ws = [vals[i - h + 1 : i + 1] for i in range(h - 1, len(vals))]
    if factor.startswith(("eq:", "fx:")):
        return [math.prod(1.0 + r for r in w) - 1.0 for w in ws]
    return [math.fsum(w) for w in ws]


@pytest.mark.criterion("C2")
@given(
    data=st.data(),
    factor=st.sampled_from(["eq:TSM", "fx:TWD", "ir:DGS10", "credit"]),
    direction=st.sampled_from(["down", "up"]),
    years=_YEARS,
    severe_factor=_SEVERE,
)
def test_c2_property_matches_order_statistic(
    data: st.DataObject, factor: str, direction: str, years: float, severe_factor: float
):
    # oracle: Requirement 2 computed in the test: k-th order statistic of the hand windows, record, record x factor.
    vals = data.draw(st.lists(_RET, min_size=1, max_size=60))
    h = data.draw(st.integers(min_value=1, max_value=min(5, len(vals))))
    assume(h < 252 * years)  # p < 1, so k <= n; the spec leaves p >= 1 open
    w = sorted(_windows(vals, factor, h))
    n = len(w)
    exact = Fraction(n * h) / (252 * Fraction(years))
    assume(exact.denominator != 1)  # keep ceil away from an exact integer, where float rounding could tip it
    k = max(1, math.ceil(exact))
    ordered = w if direction == "down" else w[::-1]
    got = tier_anchors(_series(vals), factor, h, direction, mild_return_years=years, severe_factor=severe_factor)
    assert set(got) == KEYS
    # rel 1e-12 + abs 1e-12: spec tolerance; abs covers windows that cancel to about zero
    assert got["mild"] == pytest.approx(ordered[k - 1], rel=1e-12, abs=1e-12)
    assert got["adverse"] == pytest.approx(ordered[0], rel=1e-12, abs=1e-12)
    assert got["severe"] == pytest.approx(ordered[0] * severe_factor, rel=1e-12, abs=1e-12)


@pytest.mark.criterion("C2")
@given(
    data=st.data(),
    factor=st.sampled_from(["eq:NVDA", "ir:DGS10", "credit"]),
    direction=st.sampled_from(["down", "up"]),
    years=_YEARS,
    severe_factor=_SEVERE,
)
def test_c2_property_ordering_on_the_adverse_side(
    data: st.DataObject, factor: str, direction: str, years: float, severe_factor: float
):
    # oracle: C2 ordering property: with every daily move on the adverse side (so mild and the record are too)
    # and no bound, |mild| <= |adverse| <= |severe|.
    mag = data.draw(st.lists(st.integers(min_value=0, max_value=15).map(lambda i: i / 16), min_size=1, max_size=60))
    vals = [-m for m in mag] if direction == "down" else mag
    h = data.draw(st.integers(min_value=1, max_value=min(5, len(vals))))
    assume(h < 252 * years)  # p < 1, so k <= n; the spec leaves p >= 1 open
    got = tier_anchors(_series(vals), factor, h, direction, mild_return_years=years, severe_factor=severe_factor)
    assert set(got) == KEYS
    assert all(math.isfinite(v) for v in got.values())
    # 1e-12 slack: spec tolerance on the ordering
    assert abs(got["mild"]) <= abs(got["adverse"]) + 1e-12
    assert abs(got["adverse"]) <= abs(got["severe"]) + 1e-12
    sign = -1.0 if direction == "down" else 1.0
    assert sign * got["adverse"] >= -1e-12
    if any(m > 0 for m in mag):
        assert sign * got["adverse"] > 0  # control: a nonzero move puts the record strictly on the adverse side


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    vals=st.lists(_RET, min_size=1, max_size=60),
    factor=_SUM_FACTOR,
    years=_YEARS,
    severe_factor=_SEVERE,
    bound=st.one_of(st.none(), st.sampled_from([0.25, 0.5, 1.0, 4.0])),
    h=st.integers(min_value=1, max_value=5),
)
def test_c2_metamorphic_mirroring_swaps_directions(
    vals: list[float], factor: str, years: float, severe_factor: float, bound: float | None, h: int
):
    # oracle: C2 mirroring property: for a summing factor, negating every daily move negates every window, so the
    # "down" anchors of -moves are the "up" anchors of moves with signs flipped, and vice versa.
    assume(h <= len(vals))
    assume(h < 252 * years)  # p < 1, so k <= n; the spec leaves p >= 1 open
    kw = {"mild_return_years": years, "severe_factor": severe_factor, "bound": bound}
    s, neg = _series(vals), _series([-v for v in vals])
    for d_base, d_mirror in (("up", "down"), ("down", "up")):
        base = tier_anchors(s, factor, h, d_base, **kw)
        mirror = tier_anchors(neg, factor, h, d_mirror, **kw)
        assert set(base) == KEYS
        assert set(mirror) == KEYS
        assert all(math.isfinite(v) for v in base.values())
        w = _windows(vals, factor, h)
        record = max(w) if d_base == "up" else min(w)
        # control, rel 1e-12 + abs 1e-15: the base record is the hand record (spec tolerance)
        assert base["adverse"] == pytest.approx(record, rel=1e-12, abs=1e-15)
        for key in KEYS:
            # rel 1e-12 + abs 1e-15: spec tolerance; negation is exact, abs covers zero anchors
            assert mirror[key] == pytest.approx(-base[key], rel=1e-12, abs=1e-15)
