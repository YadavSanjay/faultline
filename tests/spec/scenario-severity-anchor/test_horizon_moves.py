"""scenario-severity-anchor C1: daily moves to overlapping horizon moves.

Oracle: Requirement 1 by hand: ``eq:``/``fx:`` windows compound, ``prod(1 + r) - 1``; every other factor sums
its moves; one window per end date from the ``horizon_days``-th move on. Moves are dyadic, so the hand values
are exact in binary.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.severity import horizon_moves

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

MOVES = [0.5, -0.5, 0.25, -0.25, 0.0]
SUM_MOVES = [0.0625, -0.03125, 0.125, 0.015625, -0.0078125]


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.bdate_range("2008-09-08", periods=len(vals)))


def _pairs(s: pd.Series) -> list[tuple[str, float]]:
    return [(pd.Timestamp(k).strftime("%Y-%m-%d"), float(v)) for k, v in s.items()]


def _dates(s: pd.Series, start: int) -> list[str]:
    return [pd.Timestamp(k).strftime("%Y-%m-%d") for k in s.index[start:]]


@pytest.mark.criterion("C1")
def test_c1_point_compounding_for_eq_and_fx():
    # oracle: Requirement 1 by hand, returns 0.5, -0.5, 0.25, -0.25, 0.0:
    #   h=2: 1.5*0.5-1 = -0.25; 0.5*1.25-1 = -0.375; 1.25*0.75-1 = -0.0625; 0.75*1-1 = -0.25
    #   h=3: 1.5*0.5*1.25-1 = -0.0625; 0.5*1.25*0.75-1 = -0.53125; 1.25*0.75*1-1 = -0.0625
    s = _series(MOVES)
    for factor in ("eq:NVDA", "fx:TWD"):
        got2 = _pairs(horizon_moves(s, factor, 2))
        assert [d for d, _ in got2] == _dates(s, 1)
        # rel 1e-12 + abs 1e-15: spec tolerance; abs covers rounding near zero
        assert [v for _, v in got2] == pytest.approx([-0.25, -0.375, -0.0625, -0.25], rel=1e-12, abs=1e-15)
        got3 = _pairs(horizon_moves(s, factor, 3))
        assert [d for d, _ in got3] == _dates(s, 2)
        assert [v for _, v in got3] == pytest.approx([-0.0625, -0.53125, -0.0625], rel=1e-12, abs=1e-15)


@pytest.mark.criterion("C1")
def test_c1_point_summing_for_other_factors():
    # oracle: Requirement 1 by hand, moves 0.0625, -0.03125, 0.125, 0.015625, -0.0078125:
    #   h=2: 0.03125, 0.09375, 0.140625, 0.0078125
    #   h=3: 0.15625, 0.109375, 0.1328125
    s = _series(SUM_MOVES)
    for factor in ("ir:DGS10", "credit", "eqvol", "real:DFII10"):
        got2 = _pairs(horizon_moves(s, factor, 2))
        assert [d for d, _ in got2] == _dates(s, 1)
        # rel 1e-12: spec tolerance (dyadic sums, exact)
        assert [v for _, v in got2] == pytest.approx([0.03125, 0.09375, 0.140625, 0.0078125], rel=1e-12)
        got3 = _pairs(horizon_moves(s, factor, 3))
        assert [d for d, _ in got3] == _dates(s, 2)
        assert [v for _, v in got3] == pytest.approx([0.15625, 0.109375, 0.1328125], rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_point_window_as_long_as_the_series():
    # oracle: Requirement 1 by hand: 5 moves, h=5 -> one window at the last date;
    # compounded 1.5*0.5*1.25*0.75*1 - 1 = 0.703125 - 1 = -0.296875;
    # summed 0.0625 - 0.03125 + 0.125 + 0.015625 - 0.0078125 = 0.1640625.
    s = _series(MOVES)
    got = _pairs(horizon_moves(s, "eq:TSM", 5))
    assert [d for d, _ in got] == _dates(s, 4)
    # rel 1e-12: spec tolerance
    assert got[0][1] == pytest.approx(-0.296875, rel=1e-12)
    got_sum = _pairs(horizon_moves(_series(SUM_MOVES), "ir:DGS2", 5))
    assert len(got_sum) == 1
    assert got_sum[0][1] == pytest.approx(0.1640625, rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_refusals():
    # oracle: Requirement 1: horizon_days <= 0 or fewer moves than horizon_days raises ValueError.
    s = _series(MOVES)
    for factor in ("eq:NVDA", "ir:DGS10"):
        for h in (0, -1, 6, 20):
            with pytest.raises(ValueError, match=ANY_MESSAGE):
                horizon_moves(s, factor, h)
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        horizon_moves(_series([]), "credit", 1)


_RET = st.integers(min_value=-15, max_value=16).map(lambda i: i / 16)
_FACTOR = st.sampled_from(["eq:AAPL", "fx:JPY", "ir:DGS2", "credit", "eqvol"])


@pytest.mark.criterion("C1")
@given(vals=st.lists(_RET, min_size=1, max_size=40), factor=_FACTOR)
def test_c1_property_length_one_returns_the_moves(vals: list[float], factor: str):
    # oracle: Requirement 1: a window of one move is the move itself, both when compounding and when summing.
    s = _series(vals)
    got = _pairs(horizon_moves(s, factor, 1))
    assert [d for d, _ in got] == _dates(s, 0)
    # rel 1e-12 + abs 1e-15: spec tolerance; abs covers zero moves
    assert [v for _, v in got] == pytest.approx(vals, rel=1e-12, abs=1e-15)


@pytest.mark.criterion("C1")
@given(data=st.data(), factor=_FACTOR)
def test_c1_property_matches_window_formula(data: st.DataObject, factor: str):
    # oracle: Requirement 1 computed in the test on generated dyadic moves.
    vals = data.draw(st.lists(_RET, min_size=1, max_size=40))
    h = data.draw(st.integers(min_value=1, max_value=len(vals)))
    windows = [vals[i - h + 1 : i + 1] for i in range(h - 1, len(vals))]
    if factor.startswith(("eq:", "fx:")):
        want = [math.prod(1.0 + r for r in w) - 1.0 for w in windows]
    else:
        want = [math.fsum(w) for w in windows]
    s = _series(vals)
    got = _pairs(horizon_moves(s, factor, h))
    assert [d for d, _ in got] == _dates(s, h - 1)
    # rel 1e-12 + abs 1e-12: spec tolerance; abs covers windows that cancel to about zero
    assert [v for _, v in got] == pytest.approx(want, rel=1e-12, abs=1e-12)
