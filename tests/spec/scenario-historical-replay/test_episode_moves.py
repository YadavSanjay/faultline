"""scenario-historical-replay C1: a factor's realised move over an episode window ``(start, end]``.

Oracle: Requirement 1 by hand: ``eq:``/``fx:`` moves compound, ``prod(1 + r) - 1``; every other factor sums;
a factor with no rows in the window or a missing value in it is left out. Index dates are compared as calendar
dates whatever their type. Moves are dyadic, so the hand values are exact in binary.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.replay import episode_moves

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

NAN = float("nan")
DAYS = ["2025-01-21", "2025-01-22", "2025-01-23", "2025-01-24", "2025-01-27", "2025-01-28"]


def _str(vals: list[float], days: list[str] = DAYS) -> pd.Series:
    return pd.Series(vals, index=days[: len(vals)])


def _ts(vals: list[float], days: list[str] = DAYS) -> pd.Series:
    return pd.Series(vals, index=pd.DatetimeIndex(days[: len(vals)]))


def _dates(vals: list[float], days: list[str] = DAYS) -> pd.Series:
    return pd.Series(vals, index=[date.fromisoformat(d) for d in days[: len(vals)]])


def _book() -> dict[str, pd.Series]:
    return {
        # 01-21 .. 01-28; only 01-24 and 01-27 fall in (01-23, 01-27]
        "eq:XLK": _str([0.0, 0.5, 0.25, -0.5, 0.125, -0.25]),
        "fx:JPY": _ts([0.0, 0.125, -0.25, 0.5, -0.5, 0.0625]),
        "ir:DGS10": _str([0.0, 0.25, 0.0625, 0.03125, -0.015625, 1.0]),
        "credit": _ts([0.0, 0.0, 0.0, 0.0078125, NAN, 0.0]),  # missing value inside the window
        "eqvol": _str([0.0, NAN, 0.5, 0.0625, 0.125, 0.0]),  # missing value outside the window only
        "eq:INTC": _ts([0.0, -0.25, 0.5]),  # no rows after 01-23
        "eq:AMD": _dates([0.0, 0.0, 0.25, -0.25, 0.5, 0.25]),
    }


START, END = date(2025, 1, 23), date(2025, 1, 27)


@pytest.mark.criterion("C1")
def test_c1_point_compounds_or_sums_over_half_open_window():
    # oracle: Requirement 1 by hand over the rows 01-24 and 01-27 (01-23 excluded, 01-27 included):
    #   eq:XLK  (1 - 0.5)(1 + 0.125) - 1 = -0.4375
    #   fx:JPY  (1 + 0.5)(1 - 0.5) - 1 = -0.25
    #   ir:DGS10  0.03125 - 0.015625 = 0.015625
    #   eqvol     0.0625 + 0.125 = 0.1875 (its NaN on 01-22 is outside the window)
    #   eq:AMD  (1 - 0.25)(1 + 0.5) - 1 = 0.125
    #   credit (NaN on 01-27) and eq:INTC (no rows in window) are left out.
    got = episode_moves(_book(), START, END)
    want = {"eq:XLK": -0.4375, "fx:JPY": -0.25, "ir:DGS10": 0.015625, "eqvol": 0.1875, "eq:AMD": 0.125}
    assert set(got) == set(want)
    for f, v in want.items():
        # rel 1e-12: spec tolerance (dyadic, exact)
        assert got[f] == pytest.approx(v, rel=1e-12), f


@pytest.mark.criterion("C1")
def test_c1_point_window_edges_are_calendar_dates():
    # oracle: Requirement 1 by hand: a weekend end (Sat 01-25) keeps only Fri 01-24 for every index type;
    # a start of Wed 01-22 brings in the 01-23 row as well.
    got = episode_moves(_book(), START, date(2025, 1, 25), factors=["eq:XLK", "fx:JPY", "eq:AMD"])
    assert set(got) == {"eq:XLK", "fx:JPY", "eq:AMD"}
    # rel 1e-12: spec tolerance (dyadic, exact)
    assert got["eq:XLK"] == pytest.approx(-0.5, rel=1e-12)
    assert got["fx:JPY"] == pytest.approx(0.5, rel=1e-12)
    assert got["eq:AMD"] == pytest.approx(-0.25, rel=1e-12)
    wide = episode_moves(_book(), date(2025, 1, 22), END, factors=["eq:XLK", "ir:DGS10"])
    # (1.25)(0.5)(1.125) - 1 = -0.296875; 0.0625 + 0.03125 - 0.015625 = 0.078125
    assert wide["eq:XLK"] == pytest.approx(-0.296875, rel=1e-12)
    assert wide["ir:DGS10"] == pytest.approx(0.078125, rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_point_factors_restricts_the_keys():
    # oracle: Requirement 1: only the listed keys are considered; credit is listed but has a NaN in the window.
    got = episode_moves(_book(), START, END, factors=["ir:DGS10", "eq:XLK", "credit"])
    assert set(got) == {"ir:DGS10", "eq:XLK"}
    # rel 1e-12: spec tolerance (dyadic, exact)
    assert got["ir:DGS10"] == pytest.approx(0.015625, rel=1e-12)
    assert got["eq:XLK"] == pytest.approx(-0.4375, rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_refusals():
    # oracle: Requirement 1: start >= end raises ValueError.
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        episode_moves(_book(), END, END)
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        episode_moves(_book(), END, START)


_RET = st.integers(min_value=-48, max_value=64).map(lambda i: i / 64)
_FACTOR = st.sampled_from(["eq:NVDA", "fx:TWD", "ir:DGS2", "credit", "eqvol"])
_INDEX = st.sampled_from(["str", "ts", "date"])


def _combine(factor: str, a: float, b: float) -> float:
    if factor.startswith(("eq:", "fx:")):
        return (1.0 + a) * (1.0 + b) - 1.0
    return a + b


def _formula(factor: str, vals: list[float]) -> float:
    if factor.startswith(("eq:", "fx:")):
        return math.prod(1.0 + r for r in vals) - 1.0
    return math.fsum(vals)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(data=st.data(), factor=_FACTOR, kind=_INDEX)
def test_c1_property_window_additivity(data: st.DataObject, factor: str, kind: str):
    # oracle: C1 property: combining the moves of (start, mid] and (mid, end] (compounding or summing) gives
    # the move of (start, end]; the whole window also matches Requirement 1 computed in the test.
    vals = data.draw(st.lists(_RET, min_size=3, max_size=30))
    days = [(date(2024, 7, 1) + timedelta(days=2 * k)).isoformat() for k in range(len(vals))]
    s = {"str": _str, "ts": _ts, "date": _dates}[kind](vals, days)
    a = data.draw(st.integers(min_value=0, max_value=len(vals) - 3))
    b = data.draw(st.integers(min_value=a + 1, max_value=len(vals) - 2))
    c = data.draw(st.integers(min_value=b + 1, max_value=len(vals) - 1))
    # boundaries one calendar day after a row date, so each part holds rows a+1..b and b+1..c
    start, mid, end = (date.fromisoformat(days[i]) + timedelta(days=1) for i in (a, b, c))
    series = {factor: s}
    whole = episode_moves(series, start, end)
    left = episode_moves(series, start, mid)
    right = episode_moves(series, mid, end)
    assert set(whole) == set(left) == set(right) == {factor}
    # rel 1e-12 + abs 1e-12: spec tolerance; abs covers moves that cancel to about zero
    assert whole[factor] == pytest.approx(_combine(factor, left[factor], right[factor]), rel=1e-12, abs=1e-12)
    assert whole[factor] == pytest.approx(_formula(factor, vals[a + 1 : c + 1]), rel=1e-12, abs=1e-12)
