"""scenario-forward-log C1: realised factor moves over the rows strictly after ``as_of``.

Oracle: Requirement 1 by hand on small synthetic panels built in the tests (DatetimeIndex, ascending).
``eq:``/``fx:`` columns are simple daily returns (compound: prod(1 + r) - 1); every other column is a
daily change (sum). Rates are decimals.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.forward_log import realised_moves

NAN = float("nan")
# Six business days; 2026-03-03 is the as_of row of the point tests.
DATES = pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09"])


def _panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            #               before  as_of   window: 03-04  03-05  03-06   after
            "eq:XLK": [0.5, 9.0, 0.1, -0.2, 0.05, 7.0],
            "fx:EURUSD": [0.3, 9.0, 0.01, 0.02, -0.03, 7.0],
            "ir:UST10Y": [0.4, 9.0, 0.001, 0.002, -0.0005, 7.0],
            "cr:IG": [0.1, 9.0, 0.0001, NAN, 0.0002, 7.0],  # NaN inside the window -> left out
            "vol:VIX": [NAN, 9.0, 1.5, -0.5, 2.0, NAN],  # NaNs only outside the window -> kept
            "eq:EXTRA": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # not requested -> not returned
        },
        index=DATES,
    )


REQUESTED = ["eq:XLK", "fx:EURUSD", "ir:UST10Y", "cr:IG", "vol:VIX", "eq:MISSING"]


@pytest.mark.criterion("C1")
def test_c1_point_window_excludes_as_of_row():
    # oracle: Requirement 1 by hand; window = 03-04, 03-05, 03-06 (the as_of row 03-03 and its 9.0 excluded):
    # eq:XLK (1.1)(0.8)(1.05) - 1 = -0.076; fx:EURUSD (1.01)(1.02)(0.97) - 1 = -0.000706;
    # ir:UST10Y 0.001 + 0.002 - 0.0005 = 0.0025; vol:VIX 1.5 - 0.5 + 2.0 = 3.0;
    # cr:IG (NaN in window) and eq:MISSING (no column) left out; eq:EXTRA not requested.
    out = realised_moves(_panel(), date(2026, 3, 3), 3, REQUESTED)
    assert out is not None
    assert set(out) == {"eq:XLK", "fx:EURUSD", "ir:UST10Y", "vol:VIX"}
    # rel 1e-12: spec tolerance; three float products/sums
    assert out["eq:XLK"] == pytest.approx(1.1 * 0.8 * 1.05 - 1.0, rel=1e-12)
    assert out["eq:XLK"] == pytest.approx(-0.076, rel=1e-12)
    assert out["fx:EURUSD"] == pytest.approx(1.01 * 1.02 * 0.97 - 1.0, rel=1e-12)
    assert out["fx:EURUSD"] == pytest.approx(-0.000706, rel=1e-9)  # rel 1e-9: the decimal is exact, float isn't
    assert out["ir:UST10Y"] == pytest.approx(0.0025, rel=1e-12)
    assert out["vol:VIX"] == pytest.approx(3.0, rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_point_as_of_between_rows_and_exact_maturity():
    # oracle: Requirement 1 by hand; as_of 2026-03-07 (a Saturday, not a row) -> the only row after is 03-09
    # (horizon 1): eq:XLK 7.0 (compounded single return), ir:UST10Y 7.0. As_of 03-05 with horizon 2 has exactly
    # two rows after (03-06, 03-09) -> matured: eq:XLK (1.05)(8.0) - 1 = 7.4; ir:UST10Y -0.0005 + 7.0 = 6.9995.
    p = _panel()
    one = realised_moves(p, date(2026, 3, 7), 1, ["eq:XLK", "ir:UST10Y"])
    two = realised_moves(p, date(2026, 3, 5), 2, ["eq:XLK", "ir:UST10Y"])
    assert one is not None
    assert two is not None
    assert set(one) == {"eq:XLK", "ir:UST10Y"}
    # rel 1e-12: spec tolerance
    assert one["eq:XLK"] == pytest.approx(7.0, rel=1e-12)
    assert one["ir:UST10Y"] == pytest.approx(7.0, rel=1e-12)
    assert two["eq:XLK"] == pytest.approx(1.05 * 8.0 - 1.0, rel=1e-12)
    assert two["ir:UST10Y"] == pytest.approx(6.9995, rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_not_matured_returns_none():
    # oracle: Requirement 1 by hand: as_of 03-05 leaves two rows (03-06, 03-09) < horizon 3 -> None; as_of 03-09
    # leaves none -> None; as_of after the last row -> None. Control: as_of 03-04 with horizon 2 (03-05, 03-06) is
    # matured -> eq:XLK (0.8)(1.05) - 1 = -0.16.
    p = _panel()
    assert realised_moves(p, date(2026, 3, 5), 3, ["eq:XLK"]) is None
    assert realised_moves(p, date(2026, 3, 9), 1, ["eq:XLK"]) is None
    assert realised_moves(p, date(2026, 3, 20), 1, ["eq:XLK"]) is None
    matured = realised_moves(p, date(2026, 3, 4), 2, ["eq:XLK"])
    assert matured is not None
    assert matured["eq:XLK"] == pytest.approx(0.8 * 1.05 - 1.0, rel=1e-12)  # rel 1e-12: spec tolerance


@pytest.mark.criterion("C1")
def test_c1_only_requested_factors_and_missing_left_out():
    # oracle: Requirement 1 by hand: requesting only eq:EXTRA (constant 0.1) over two rows after 03-02
    # -> (1.1)^2 - 1 = 0.21; cr:IG over 03-04..03-04 (horizon 1 after 03-03) has no NaN -> 0.0001 kept.
    p = _panel()
    out = realised_moves(p, date(2026, 3, 2), 2, ["eq:EXTRA", "eq:NOPE"])
    one = realised_moves(p, date(2026, 3, 3), 1, ["cr:IG"])
    assert out is not None
    assert one is not None
    assert set(out) == {"eq:EXTRA"}
    # rel 1e-12: spec tolerance
    assert out["eq:EXTRA"] == pytest.approx(1.1 * 1.1 - 1.0, rel=1e-12)
    assert set(one) == {"cr:IG"}
    assert one["cr:IG"] == pytest.approx(0.0001, rel=1e-12)


_RET = st.integers(min_value=-60, max_value=80).map(lambda i: i / 128)  # dyadic, > -1
_KEYS = ["eq:A", "fx:B", "ir:C", "cr:D"]


@st.composite
def _panel_case(draw: st.DrawFn):
    n_before = draw(st.integers(min_value=0, max_value=3))
    horizon = draw(st.integers(min_value=1, max_value=6))
    n_after = draw(st.integers(min_value=0, max_value=3))
    n = n_before + 1 + horizon + n_after
    cols = {k: [draw(_RET) for _ in range(n)] for k in _KEYS}
    idx = pd.bdate_range("2026-01-05", periods=n)
    return pd.DataFrame(cols, index=idx), n_before, horizon


@pytest.mark.criterion("C1")
@given(case=_panel_case())
def test_c1_property_matches_requirement_formula(case):
    # oracle: Requirement 1 computed by hand in the test: rows strictly after the as_of row, first `horizon`;
    # eq:/fx: math.prod(1 + r) - 1, others the plain sum.
    panel, n_before, horizon = case
    as_of = panel.index[n_before].date()
    out = realised_moves(panel, as_of, horizon, _KEYS)
    assert out is not None
    assert set(out) == set(_KEYS)
    window = panel.iloc[n_before + 1 : n_before + 1 + horizon]
    for k in _KEYS:
        vals = [float(v) for v in window[k]]
        expected = math.prod(1.0 + v for v in vals) - 1.0 if k.startswith(("eq:", "fx:")) else math.fsum(vals)
        # abs 1e-12 + rel 1e-12: spec tolerance; abs guards a sum that cancels to ~0
        assert out[k] == pytest.approx(expected, rel=1e-12, abs=1e-12)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(case=_panel_case(), pad=st.integers(min_value=1, max_value=4), fill=_RET)
def test_c1_metamorphic_rows_outside_window_do_not_matter(case, pad: int, fill: float):
    # oracle: Requirement 1: only the first `horizon` rows after as_of count, so overwriting every row outside
    # the window (before, at and after as_of, including with NaN) and appending later rows leaves the result
    # unchanged.
    panel, n_before, horizon = case
    as_of = panel.index[n_before].date()
    base = realised_moves(panel, as_of, horizon, _KEYS)
    changed = panel.copy()
    outside = np.r_[0 : n_before + 1, n_before + 1 + horizon : len(panel)]
    changed.iloc[outside, :] = fill
    changed.iloc[n_before, 0] = NAN  # a NaN on the as_of row is outside the window
    extra_idx = pd.bdate_range(panel.index[-1] + pd.offsets.BDay(1), periods=pad)
    extra = pd.DataFrame({k: [fill] * pad for k in _KEYS}, index=extra_idx)
    changed = pd.concat([changed, extra])
    out = realised_moves(changed, as_of, horizon, _KEYS)
    assert base is not None
    assert out is not None
    assert set(out) == set(base) == set(_KEYS)
    for k in _KEYS:
        # rel 1e-12: same window, same arithmetic
        assert out[k] == pytest.approx(base[k], rel=1e-12, abs=1e-15)
