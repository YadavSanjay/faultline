"""Offline unit tests for option-implied signal derivation (market-intelligence B1).

Pure-function tests on synthetic chains — no network, no data/ dependency.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.marketdata.options.signals import SIGNAL_COLUMNS, compute_signals, realized_vol


def _chain() -> pd.DataFrame:
    """A well-formed 2-expiry chain: 30d richer than 90d (inverted), put skew at 25Δ.

    Columns match what the CBOE downloader writes.
    """
    rows = [
        # dte=30 (front), ATM IV 0.40, 25Δ put richer than 25Δ call -> positive skew
        ("call", 100.0, 0.50, 0.40, 5.0, 300, 40),
        ("call", 110.0, 0.25, 0.38, 2.0, 500, 60),
        ("put",  100.0, -0.50, 0.40, 5.0, 400, 50),
        ("put",  90.0, -0.25, 0.45, 2.0, 900, 90),
        # dte=90 (back), ATM IV 0.30
        ("call", 100.0, 0.50, 0.30, 8.0, 100, 10),
        ("call", 115.0, 0.25, 0.29, 3.0, 150, 15),
        ("put",  100.0, -0.50, 0.30, 8.0, 120, 12),
        ("put",  85.0, -0.25, 0.33, 3.0, 200, 20),
    ]
    df = pd.DataFrame(rows, columns=["put_call", "strike", "delta", "iv", "mid", "open_interest", "volume"])
    df["days_to_expiry"] = [30, 30, 30, 30, 90, 90, 90, 90]
    return df


def test_signal_row_has_the_declared_schema():
    row = compute_signals(_chain(), "TEST", "2026-07-10")
    assert set(row.keys()) == set(SIGNAL_COLUMNS)


def test_atm_iv_and_inverted_term_structure():
    row = compute_signals(_chain(), "TEST", "2026-07-10")
    assert row["atm_iv_30d"] == pytest.approx(0.40, abs=1e-9)
    assert row["atm_iv_90d"] == pytest.approx(0.30, abs=1e-9)
    assert row["term_slope"] == pytest.approx(-0.10, abs=1e-9)   # back - front
    assert row["term_inverted"] is True
    assert row["term_ratio"] == pytest.approx(0.40 / 0.30, abs=1e-9)


def test_25delta_skew_is_put_minus_call():
    row = compute_signals(_chain(), "TEST", "2026-07-10")
    # 30d: put25 IV 0.45 - call25 IV 0.38 = +0.07
    assert row["skew_25d_30d"] == pytest.approx(0.07, abs=1e-9)


def test_positioning_ratios_and_concentration():
    row = compute_signals(_chain(), "TEST", "2026-07-10")
    # put OI 400+900+120+200=1620 ; call OI 300+500+100+150=1050
    assert row["pc_oi_ratio"] == pytest.approx(1620 / 1050, abs=1e-9)
    # volume: put 50+90+12+20=172 ; call 40+60+10+15=125
    assert row["pc_volume_ratio"] == pytest.approx(172 / 125, abs=1e-9)
    assert 0.0 < float(row["oi_hhi"]) <= 1.0
    assert 0.0 < float(row["oi_top5_share"]) <= 1.0
    assert row["n_expiries"] == 2


def test_vrp_uses_realized_vol_when_history_present():
    # A flat-ish random walk -> some realized vol; VRP = atm_iv_30d - realized_vol_20d
    rng = np.random.default_rng(0)
    closes = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 60))))
    row = compute_signals(_chain(), "TEST", "2026-07-10", closes=closes)
    assert row["realized_vol_20d"] is not None
    assert row["vrp_20d"] == pytest.approx(float(row["atm_iv_30d"]) - float(row["realized_vol_20d"]), abs=1e-9)


def test_no_history_leaves_vrp_null_not_raising():
    row = compute_signals(_chain(), "TEST", "2026-07-10", closes=None)
    assert row["realized_vol_20d"] is None
    assert row["vrp_20d"] is None
    assert row["atm_iv_30d"] is not None          # the chain-only signals still populate


def test_thin_one_sided_chain_degrades_gracefully():
    """A calls-only, single-expiry chain must not raise; unavailable signals are None."""
    df = pd.DataFrame({
        "put_call": ["call", "call"],
        "strike": [100.0, 110.0],
        "delta": [0.50, 0.25],
        "iv": [0.40, 0.38],
        "mid": [5.0, 2.0],
        "open_interest": [100, 200],
        "volume": [10, 20],
        "days_to_expiry": [30, 30],
    })
    row = compute_signals(df, "THIN", "2026-07-10")
    assert row["atm_iv_30d"] is not None          # one expiry -> flat interpolation
    assert row["atm_iv_90d"] is None               # <2 expiries -> no back tenor
    assert row["term_inverted"] is None            # no second tenor -> term structure unknown
    assert row["skew_25d_30d"] is None             # no puts -> no skew
    assert row["pc_oi_ratio"] == 0.0               # calls only -> zero put/call OI (meaningful, not null)


def test_empty_after_filtering_returns_stub_row():
    df = pd.DataFrame({
        "put_call": ["call"], "strike": [100.0], "delta": [0.5], "iv": [5.0],  # iv > max
        "mid": [1.0], "open_interest": [1], "volume": [1], "days_to_expiry": [30],
    })
    row = compute_signals(df, "X", "2026-07-10")
    assert row["n_contracts"] == 0
    assert row["atm_iv_30d"] is None


def test_realized_vol_needs_enough_points():
    assert realized_vol(pd.Series([100.0, 101.0, 100.5]), window=20) is None
    s = pd.Series(100.0 * np.exp(np.cumsum(np.full(30, 0.0))))  # constant -> zero vol
    assert realized_vol(s, window=20) == pytest.approx(0.0, abs=1e-12)
