"""long-history-ingest C3: a level series to a factor's daily moves in canonical units.

Oracle: Requirement 4 by hand: ``eq:``/``fx:`` take simple returns ``L_t / L_{t-1} - 1``; every other
factor (``ir:``, ``credit``, ``eqvol``) the percent-point change divided by 100; the first point is dropped.
Scaling the levels by a positive constant is a property.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.long_history import level_moves


def _pairs(s: pd.Series) -> list[tuple[str, float]]:
    return [(pd.Timestamp(k).strftime("%Y-%m-%d"), float(v)) for k, v in s.items()]


IDX = ["2008-09-12", "2008-09-15", "2008-09-16", "2008-09-17"]


@pytest.mark.criterion("C3")
def test_c3_point_returns_for_eq_and_fx():
    # oracle: Requirement 4 by hand: 100 -> 110 -> 99 -> 99: +0.1, -0.1, 0.0 (first point dropped).
    levels = pd.Series([100.0, 110.0, 99.0, 99.0], index=IDX)
    for factor in ("eq:NVDA", "fx:JPY"):
        got = _pairs(level_moves(levels, factor))
        assert [d for d, _ in got] == IDX[1:]
        # rel 1e-12 / abs 1e-15: spec tolerance; abs covers the exact-zero move
        assert [v for _, v in got] == pytest.approx([0.1, -0.1, 0.0], rel=1e-12, abs=1e-15)


@pytest.mark.criterion("C3")
def test_c3_point_percent_point_changes_for_other_factors():
    # oracle: Requirement 4 by hand: yields 3.72 -> 3.47 -> 3.41 -> 3.65 (percent) give -0.0025, -0.0006, +0.0024;
    # VIX 25.66 -> 31.7 -> 30.3 -> 36.22 gives +0.0604, -0.014, +0.0592.
    rates = pd.Series([3.72, 3.47, 3.41, 3.65], index=IDX)
    vix = pd.Series([25.66, 31.7, 30.3, 36.22], index=IDX)
    for factor in ("ir:DGS10", "credit"):
        got = _pairs(level_moves(rates, factor))
        assert [d for d, _ in got] == IDX[1:]
        # rel 1e-9: decimal inputs are not exact in binary; the spec's 1e-12 holds against the float formula below
        assert [v for _, v in got] == pytest.approx([-0.0025, -0.0006, 0.0024], rel=1e-9)
        assert [v for _, v in got] == pytest.approx(
            [(3.47 - 3.72) / 100, (3.41 - 3.47) / 100, (3.65 - 3.41) / 100], rel=1e-12
        )
    got = _pairs(level_moves(vix, "eqvol"))
    assert [v for _, v in got] == pytest.approx(
        [(31.7 - 25.66) / 100, (30.3 - 31.7) / 100, (36.22 - 30.3) / 100], rel=1e-12
    )  # rel 1e-12: spec tolerance
    assert [v for _, v in got] == pytest.approx([0.0604, -0.014, 0.0592], rel=1e-9)  # rel 1e-9: decimal inputs


_LEVEL = st.integers(min_value=1, max_value=100_000).map(lambda i: i / 64)
_FACTOR = st.sampled_from(["eq:AAPL", "fx:TWD", "ir:DGS2", "credit", "eqvol"])


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=[(date(1999, 1, 4) + timedelta(days=k)).isoformat() for k in range(len(vals))])


def _expected(vals: list[float], factor: str) -> list[float]:
    if factor.startswith(("eq:", "fx:")):
        return [b / a - 1.0 for a, b in pairwise(vals)]
    return [(b - a) / 100.0 for a, b in pairwise(vals)]


@pytest.mark.criterion("C3")
@given(vals=st.lists(_LEVEL, min_size=2, max_size=40), factor=_FACTOR)
def test_c3_property_matches_formula(vals: list[float], factor: str):
    # oracle: Requirement 4 computed in the test.
    s = _series(vals)
    got = _pairs(level_moves(s, factor))
    assert [d for d, _ in got] == [pd.Timestamp(k).strftime("%Y-%m-%d") for k in s.index[1:]]
    # rel 1e-12 + abs 1e-15: spec tolerance; abs covers zero moves
    assert [v for _, v in got] == pytest.approx(_expected(vals, factor), rel=1e-12, abs=1e-15)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(
    vals=st.lists(_LEVEL, min_size=2, max_size=40),
    factor=_FACTOR,
    c=st.integers(min_value=1, max_value=4000).map(lambda i: i / 16),
)
def test_c3_metamorphic_scaling_levels(vals: list[float], factor: str, c: float):
    # oracle: Requirement 4: L -> cL leaves cL_t/cL_{t-1} - 1 unchanged and scales (cL_t - cL_{t-1})/100 by c.
    base = [v for _, v in _pairs(level_moves(_series(vals), factor))]
    scaled = [v for _, v in _pairs(level_moves(_series([c * v for v in vals]), factor))]
    assert len(base) == len(vals) - 1
    want = base if factor.startswith(("eq:", "fx:")) else [c * b for b in base]
    # rel 1e-9 + abs 1e-12: one extra rounding from the scaled product, relative to the level size
    assert scaled == pytest.approx(want, rel=1e-9, abs=1e-12 * max(1.0, c))
