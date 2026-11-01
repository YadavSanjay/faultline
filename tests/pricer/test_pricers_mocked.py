"""Pricer correctness on mock market data (no CSV files).

Demonstrates the mockability of the domain layer and locks in golden behaviour /
sanity bounds for all four rates trades.
"""

from __future__ import annotations

import math

import pytest
from conftest import mock_resolve
from src.pricer.input.rates.instruments import (
    BermudanSwaptionTrade,
    CapTrade,
    EuropeanSwaptionTrade,
    OvernightIndexedSwapTrade,
)
from src.pricer.ql.rates.pricer_factory import get_pricer


def _price(trade, ctx, curve_data, vol_data):
    mds = mock_resolve(trade, ctx, curve_data, vol_data)
    return get_pricer(trade).price(trade, mds, ctx)


# --------------------------------------------------------------------------- #
# Trade #22 — OIS swap
# --------------------------------------------------------------------------- #
def test_ois_swap_payer_sign_and_dv01(ctx, curve_data, vol_data):
    # fixed 3.80% below the ~4.0% 10Y fair rate -> payer is in-the-money (PV>0)
    t = OvernightIndexedSwapTrade("T22", "10Y payer", "USD", 50_000_000, fixed_rate=0.038)
    r = _price(t, ctx, curve_data, vol_data)
    assert r.pv > 0
    assert r.measures["fair_rate"] > 0.038
    # 10Y $50mm DV01 is roughly $40k/bp
    assert 30_000 < r.measures["dv01"] < 55_000
    assert math.isclose(r.leg_pvs["fixed"] + r.leg_pvs["float"], r.pv, rel_tol=1e-9, abs_tol=1.0)


def test_ois_swap_struck_at_fair_is_flat(ctx, curve_data, vol_data):
    t0 = OvernightIndexedSwapTrade("T22f", "10Y", "USD", 50_000_000, fixed_rate=0.04)
    fair = _price(t0, ctx, curve_data, vol_data).measures["fair_rate"]
    t = OvernightIndexedSwapTrade("T22f", "10Y", "USD", 50_000_000, fixed_rate=fair)
    assert abs(_price(t, ctx, curve_data, vol_data).pv) < 5.0


# --------------------------------------------------------------------------- #
# Trade #23 — European swaption
# --------------------------------------------------------------------------- #
def test_european_swaption_positive_and_vega(ctx, curve_data, vol_data):
    t = EuropeanSwaptionTrade("T23", "5x10 payer", "USD", 50_000_000)
    r = _price(t, ctx, curve_data, vol_data)
    assert r.pv > 0
    assert r.measures["vega_1bp"] > 0          # long option => positive vega
    assert r.measures["normal_vol_bp"] == pytest.approx(90.0)


def test_european_swaption_zero_vol_is_intrinsic(ctx, curve_data):
    from src.pricer.input.common.market_data import FlatNormalVolData
    zero_vol = FlatNormalVolData("USD", 1e-7, ctx.valuation_date)
    t = EuropeanSwaptionTrade("T23i", "5x10 payer", "USD", 50_000_000)
    r = _price(t, ctx, curve_data, zero_vol)
    # ATM payer at ~zero vol => ~zero PV (intrinsic of ATM is 0)
    assert r.pv < 5_000


# --------------------------------------------------------------------------- #
# Trade #24 — Bermudan swaption
# --------------------------------------------------------------------------- #
def test_bermudan_calibration_and_crosscheck(ctx, curve_data, vol_data):
    t = BermudanSwaptionTrade("T24", "2x5 receiver", "USD", 50_000_000)
    r = _price(t, ctx, curve_data, vol_data)
    assert r.pv > 0
    # tree and FD engines must agree closely
    assert r.measures["tree_fd_rel_diff"] < 0.02
    # co-terminal calibration must fit the grid tightly
    assert r.measures["calibration_rmse"] < 1e-3
    assert r.measures["hw_sigma"] > 0
    assert r.measures["n_exercise_dates"] == 5


# --------------------------------------------------------------------------- #
# Trade #25 — Cap
# --------------------------------------------------------------------------- #
def test_cap_positive_and_vega(ctx, curve_data, vol_data):
    t = CapTrade("T25", "1Y SOFR cap", "USD", 50_000_000)
    r = _price(t, ctx, curve_data, vol_data)
    assert r.pv > 0
    assert r.measures["vega_1bp"] > 0
    assert r.measures["n_caplets"] >= 3
