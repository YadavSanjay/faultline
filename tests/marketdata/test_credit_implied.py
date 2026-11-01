"""Tests for the option-implied credit models (market-intelligence B3, signal 6).

Merton and CreditGrades are exercised on genuinely-levered synthetic firms, so the tests prove the
models produce meaningful, correctly-ordered spreads — the portfolio's near-zero spreads on the
live data are a property of that data's low per-share leverage, not of the models.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.marketdata.options.credit_implied import (
    build_credit_table,
    creditgrades_spread,
    merton_implied_spread,
)


# --------------------------------------------------------------------------- #
# Merton
# --------------------------------------------------------------------------- #
def test_merton_zero_debt_returns_none():
    assert merton_implied_spread(1e11, 0.4, 0.0) is None


def test_merton_degenerate_inputs_return_none():
    assert merton_implied_spread(0.0, 0.4, 1e9) is None
    assert merton_implied_spread(1e9, 0.0, 1e9) is None


def test_merton_higher_leverage_widens_spread_and_lowers_dd():
    low = merton_implied_spread(1000.0, 0.4, 100.0)     # debt small vs equity
    high = merton_implied_spread(100.0, 0.4, 400.0)     # debt large vs equity
    assert low is not None and high is not None
    assert high.spread_bp > low.spread_bp
    assert high.distance_to_default < low.distance_to_default
    assert 0.0 <= high.default_prob <= 1.0


# --------------------------------------------------------------------------- #
# CreditGrades
# --------------------------------------------------------------------------- #
def test_creditgrades_nonzero_for_a_levered_firm():
    # debt-per-share 1.5x spot, 40% vol -> a real, non-trivial spread
    s = creditgrades_spread(100.0, 0.40, 150.0)
    assert s is not None
    assert s > 50.0                                     # basic Merton would be ~0 here


def test_creditgrades_monotone_in_leverage_and_vol():
    base = creditgrades_spread(100.0, 0.40, 100.0)
    more_debt = creditgrades_spread(100.0, 0.40, 160.0)
    more_vol = creditgrades_spread(100.0, 0.60, 100.0)
    assert base is not None and more_debt is not None and more_vol is not None
    assert more_debt > base
    assert more_vol > base


def test_creditgrades_degenerate_inputs_return_none():
    assert creditgrades_spread(0.0, 0.4, 100.0) is None
    assert creditgrades_spread(100.0, 0.0, 100.0) is None
    assert creditgrades_spread(100.0, 0.4, 0.0) is None


# --------------------------------------------------------------------------- #
# build_credit_table
# --------------------------------------------------------------------------- #
def _fundamentals() -> dict:
    return {
        "horizon_years": 1.0, "risk_free_rate": 0.04, "recovery_rate": 0.40,
        "issuers": {
            "LEVR": {"total_debt_usd": 1.5e11, "shares_outstanding": 1e9, "cds_reference_bp": 200.0},
            "SAFE": {"total_debt_usd": 1e9, "shares_outstanding": 1e10, "cds_reference_bp": None},
        },
    }


def _signals() -> pd.DataFrame:
    return pd.DataFrame({
        "snapshot_date": ["2026-07-10", "2026-07-10", "2026-07-10"],
        "underlying": ["LEVR", "SAFE", "NOTLISTED"],   # NOTLISTED has no fundamentals -> skipped
        "atm_iv_90d": [0.45, 0.30, 0.5],
        "atm_iv_30d": [0.5, 0.32, 0.5],
        "rnd_spot": [100.0, 200.0, 50.0],
    })


def test_build_credit_table_skips_names_without_fundamentals():
    tbl = build_credit_table(_signals(), _fundamentals())
    assert set(tbl["underlying"]) == {"LEVR", "SAFE"}       # NOTLISTED dropped


def test_build_credit_table_computes_basis_only_when_cds_present():
    tbl = build_credit_table(_signals(), _fundamentals()).set_index("underlying")
    # LEVR: debt 150bn vs equity 100bn -> levered -> non-trivial CreditGrades spread + a basis
    assert tbl.loc["LEVR", "cg_spread_bp"] > 0
    assert tbl.loc["LEVR", "basis_bp"] == pytest.approx(
        tbl.loc["LEVR", "cg_spread_bp"] - 200.0, abs=1e-6)
    # SAFE: no CDS reference -> basis is null
    assert pd.isna(tbl.loc["SAFE", "basis_bp"])


def test_build_credit_table_falls_back_to_30d_vol():
    sig = _signals()
    sig.loc[sig["underlying"] == "LEVR", "atm_iv_90d"] = None
    tbl = build_credit_table(sig, _fundamentals()).set_index("underlying")
    assert tbl.loc["LEVR", "equity_vol"] == pytest.approx(0.5)   # used atm_iv_30d
