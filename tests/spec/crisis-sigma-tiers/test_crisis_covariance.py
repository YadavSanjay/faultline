"""crisis-sigma-tiers C1: the crisis Σ is the shrunk sample covariance of the rows inside the crisis windows.

Oracle: numpy ``cov`` (ddof 1) and ``mean`` on the rows each test selects by hand (an explicit date list, or
an ISO-string comparison against the windows), with every off-diagonal entry scaled by ``1 - shrinkage``.
Panel values are dyadic, so the hand results are reproduced to rounding.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.covariance import crisis_covariance

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

FACTORS = ["EQ_XLK", "EQ_NVDA", "IR_UST_1Y"]

# Ten business days, 2022-01-03 (Mon) .. 2022-01-14 (Fri); dyadic moves.
DATES = ["2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07",
         "2022-01-10", "2022-01-11", "2022-01-12", "2022-01-13", "2022-01-14"]
VALUES = [
    [0.5, 0.25, -0.125],
    [-0.25, -0.5, 0.0625],
    [0.125, 0.375, -0.25],
    [-0.375, -0.75, 0.125],
    [0.25, 0.125, 0.0],
    [-0.0625, 0.5, -0.0625],
    [0.75, -0.25, 0.25],
    [-0.5, 0.0, 0.5],
    [0.0, 0.625, -0.375],
    [0.375, -0.125, 0.1875],
]
# Two overlapping windows (2022-01-06 is in both) and one the history doesn't reach.
WINDOWS = [("2022-01-04", "2022-01-06"), ("2022-01-06", "2022-01-10"), ("2018-10-01", "2018-12-31")]
# Rows inside the windows by hand: 01-04, 01-05, 01-06 | 01-06, 01-07, 01-10 (01-08/09 is a weekend).
SELECTED = ["2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07", "2022-01-10"]


def _panel(index: list[str] | pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(VALUES, index=index, columns=FACTORS)


def _expected(rows: np.ndarray, shrinkage: float) -> tuple[np.ndarray, np.ndarray]:
    cov = np.atleast_2d(np.cov(rows, rowvar=False, ddof=1))
    off = ~np.eye(cov.shape[0], dtype=bool)
    shrunk = cov.copy()
    shrunk[off] = cov[off] * (1.0 - shrinkage)
    return shrunk, rows.mean(axis=0)


@pytest.mark.criterion("C1")
def test_c1_point_overlapping_and_empty_windows():
    # oracle: numpy cov/mean on the five rows selected by hand (SELECTED), off-diagonals x (1 - 0.25);
    #   coverage by hand: window 1 -> 3 rows, window 2 -> 3 rows, 2018-Q4 -> 0; n_obs 5 (01-06 counted once).
    panel = _panel(pd.DatetimeIndex(pd.to_datetime(DATES)))
    rows = np.array([VALUES[DATES.index(d)] for d in SELECTED])
    exp_cov, exp_mean = _expected(rows, 0.25)
    cov, coverage = crisis_covariance(panel, WINDOWS, shrinkage=0.25, min_days=5)
    assert cov.factors == tuple(FACTORS)
    assert cov.n_obs == 5
    # rel 1e-12: spec tolerance; abs 1e-15 only guards entries that are 0 by hand
    assert cov.cov == pytest.approx(exp_cov, rel=1e-12, abs=1e-15)
    assert cov.mean == pytest.approx(exp_mean, rel=1e-12, abs=1e-15)
    assert coverage == {"2022-01-04..2022-01-06": 3, "2022-01-06..2022-01-10": 3, "2018-10-01..2018-12-31": 0}


@pytest.mark.criterion("C1")
def test_c1_point_date_string_index_and_no_shrinkage():
    # oracle: numpy cov/mean on the rows selected by hand; shrinkage 0 keeps the raw sample covariance.
    #   Window 2022-01-12..2022-01-14 (ends inclusive) -> 01-12, 01-13, 01-14; window 2022-01-03..2022-01-03 -> 01-03.
    panel = _panel(DATES)
    windows = [("2022-01-12", "2022-01-14"), ("2022-01-03", "2022-01-03")]
    rows = np.array([VALUES[DATES.index(d)] for d in ["2022-01-03", "2022-01-12", "2022-01-13", "2022-01-14"]])
    exp_cov, exp_mean = _expected(rows, 0.0)
    cov, coverage = crisis_covariance(panel, windows, shrinkage=0.0, min_days=4)
    assert cov.factors == tuple(FACTORS)
    assert cov.n_obs == 4
    # rel 1e-12: spec tolerance; abs 1e-15 only guards entries that are 0 by hand
    assert cov.cov == pytest.approx(exp_cov, rel=1e-12, abs=1e-15)
    assert cov.mean == pytest.approx(exp_mean, rel=1e-12, abs=1e-15)
    assert coverage == {"2022-01-12..2022-01-14": 3, "2022-01-03..2022-01-03": 1}


@pytest.mark.criterion("C1")
def test_c1_refusals_and_min_days_boundary():
    # oracle: Requirement 1: fewer than min_days rows in total, or shrinkage outside [0, 1], raises ValueError;
    #   exactly min_days rows (5 by hand) is accepted, as are shrinkage 0 and 1.
    panel = _panel(pd.DatetimeIndex(pd.to_datetime(DATES)))
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        crisis_covariance(panel, WINDOWS, shrinkage=0.25, min_days=6)
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        crisis_covariance(panel, [("2018-10-01", "2018-12-31")], shrinkage=0.25, min_days=2)
    for bad in (-0.125, 1.125):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            crisis_covariance(panel, WINDOWS, shrinkage=bad, min_days=5)
    for ok in (0.0, 1.0):
        cov, _ = crisis_covariance(panel, WINDOWS, shrinkage=ok, min_days=5)
        assert cov.n_obs == 5


# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #
N_ROWS = 30
P_DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2022-01-03", periods=N_ROWS)]
# Window ends range over calendar days reaching before and after the panel.
CAL_DAYS = [d.strftime("%Y-%m-%d") for d in pd.date_range("2021-12-20", "2022-02-28")]

dyadic = st.integers(min_value=-64, max_value=64).map(lambda i: i / 32.0)
shrink = st.integers(min_value=0, max_value=16).map(lambda i: i / 16.0)
window = st.tuples(st.integers(0, len(CAL_DAYS) - 1), st.integers(0, len(CAL_DAYS) - 1)).map(
    lambda ij: (CAL_DAYS[min(ij)], CAL_DAYS[max(ij)]))
windows_st = st.lists(window, min_size=1, max_size=4, unique=True)
panel_values = st.lists(st.lists(dyadic, min_size=3, max_size=3), min_size=N_ROWS, max_size=N_ROWS)


def _p_panel(values: list[list[float]]) -> pd.DataFrame:
    return pd.DataFrame(values, index=pd.DatetimeIndex(pd.to_datetime(P_DATES)), columns=FACTORS)


def _mask(windows: list[tuple[str, str]]) -> np.ndarray:
    # ISO date strings compare in date order; both ends inclusive.
    return np.array([any(s <= d <= e for s, e in windows) for d in P_DATES])


@pytest.mark.criterion("C1")
@given(values=panel_values, windows=windows_st, s=shrink)
def test_c1_property_matches_hand_selection(values, windows, s):
    # oracle: numpy cov/mean on the rows an ISO-string comparison selects; coverage counted by hand.
    panel = _p_panel(values)
    mask = _mask(windows)
    n = int(mask.sum())
    if n < 2:
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            crisis_covariance(panel, windows, shrinkage=s, min_days=2)
        return
    exp_cov, exp_mean = _expected(np.array(values)[mask], s)
    cov, coverage = crisis_covariance(panel, windows, shrinkage=s, min_days=2)
    assert cov.n_obs == n
    # rel 1e-12: spec tolerance; abs 1e-14 absorbs rounding on entries that cancel to ~0
    assert cov.cov == pytest.approx(exp_cov, rel=1e-12, abs=1e-14)
    assert cov.mean == pytest.approx(exp_mean, rel=1e-12, abs=1e-14)
    assert coverage == {f"{a}..{b}": sum(a <= d <= b for d in P_DATES) for a, b in windows}


@pytest.mark.criterion("C1")
@given(values=panel_values, windows=windows_st, s1=shrink, s2=shrink)
def test_c1_property_diagonal_independent_of_shrinkage(values, windows, s1, s2):
    # oracle: property: shrinkage keeps the variances; both equal numpy's ddof-1 variance of the selected rows.
    panel = _p_panel(values)
    mask = _mask(windows)
    if mask.sum() < 2:
        return
    var = np.var(np.array(values)[mask], axis=0, ddof=1)
    a, _ = crisis_covariance(panel, windows, shrinkage=s1, min_days=2)
    b, _ = crisis_covariance(panel, windows, shrinkage=s2, min_days=2)
    # rel 1e-12: spec tolerance; abs 1e-14 absorbs rounding on a constant column
    assert np.diag(a.cov) == pytest.approx(np.diag(b.cov), rel=1e-12, abs=1e-14)
    assert np.diag(a.cov) == pytest.approx(var, rel=1e-12, abs=1e-14)


@pytest.mark.criterion("C1")
@given(values=panel_values, other=panel_values, windows=windows_st, s=shrink, seed=st.integers(0, 2**16))
def test_c1_property_window_order_and_outside_rows_irrelevant(values, other, windows, s, seed):
    # oracle: metamorphic: permuting the windows, and overwriting every row outside them, changes nothing.
    panel = _p_panel(values)
    mask = _mask(windows)
    if mask.sum() < 2:
        return
    base, base_cov = crisis_covariance(panel, windows, shrinkage=s, min_days=2)
    perm = [windows[i] for i in np.random.default_rng(seed).permutation(len(windows))]
    moved = panel.copy()
    moved.loc[~mask] = np.array(other)[~mask]
    for p, w in ((panel, perm), (moved, windows), (moved, perm)):
        got, got_cov = crisis_covariance(p, w, shrinkage=s, min_days=2)
        assert got.n_obs == base.n_obs == int(mask.sum())
        # rel 1e-12: spec tolerance; abs 1e-14 absorbs rounding on entries that cancel to ~0
        assert got.cov == pytest.approx(base.cov, rel=1e-12, abs=1e-14)
        assert got.mean == pytest.approx(base.mean, rel=1e-12, abs=1e-14)
        assert got_cov == base_cov


@pytest.mark.criterion("C1")
@given(values=panel_values, windows=windows_st)
def test_c1_property_full_shrinkage_is_diagonal(values, windows):
    # oracle: property: at shrinkage 1 every off-diagonal is x 0; the diagonal is numpy's ddof-1 variance.
    panel = _p_panel(values)
    mask = _mask(windows)
    if mask.sum() < 2:
        return
    var = np.var(np.array(values)[mask], axis=0, ddof=1)
    cov, _ = crisis_covariance(panel, windows, shrinkage=1.0, min_days=2)
    # abs 1e-15: the off-diagonals are 0 by construction; rel 1e-12 spec tolerance on the variances
    assert cov.cov == pytest.approx(np.diag(var), rel=1e-12, abs=1e-15)
