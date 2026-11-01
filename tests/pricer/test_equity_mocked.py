"""Equity-option pricer correctness on mock market data (no CSV files).

Validates the BSM pricing, mixed European/American engines, multi-leg structures
and greeks against hand-built domain market data, plus the model-independent
European put-call parity identity.
"""

from __future__ import annotations

import math
from datetime import date

import pytest
import QuantLib as ql
from src.pricer.input.common.conventions import IndexConvention
from src.pricer.input.common.enums import (
    ExerciseStyle,
    MarketDataKind,
    OptionRight,
)
from src.pricer.input.common.market_data import (
    EquityMarketData,
    EquityVolSurface,
    MarketDataSet,
    ParOISCurveData,
)
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.input.equity.instruments import EquityOptionTrade, OptionLeg
from src.pricer.ql.common import conventions as cv
from src.pricer.ql.common.curve_builder import build_ois_curve
from src.pricer.ql.equity.pricer_factory import get_pricer

VDATE = date(2026, 6, 17)
SPOT = 200.0
QDIV = 0.01
FLATVOL = 0.30
EXPIRY = date(2026, 12, 18)


@pytest.fixture
def ctx():
    return ValuationContext(valuation_date=VDATE)


@pytest.fixture
def curve_data():
    return ParOISCurveData("USD", IndexConvention.usd_sofr(), VDATE,
                           ("3M", "6M", "1Y", "2Y", "5Y", "10Y"),
                           (0.040, 0.040, 0.040, 0.040, 0.040, 0.040), source="unit-test")


@pytest.fixture
def equity():
    # flat vol surface: constant IV across a wide strike/expiry grid
    days, strikes, ivs = [], [], []
    for d in (30, 90, 180, 400):
        for k in range(50, 401, 10):
            days.append(d)
            strikes.append(float(k))
            ivs.append(FLATVOL)
    surf = EquityVolSurface("XYZ", tuple(days), tuple(strikes), tuple(ivs), VDATE)
    return EquityMarketData("XYZ", "USD", SPOT, QDIV, VDATE, surf)


def _resolve(trade, ctx, curve_data, equity) -> MarketDataSet:
    mds = MarketDataSet(valuation_date=ctx.valuation_date)
    for req in trade.market_data_requirements(ctx).requirements:
        if req.kind == MarketDataKind.OIS_CURVE:
            mds.put(req, curve_data, curve_data.as_of_date)
        elif req.kind == MarketDataKind.EQUITY_MARKET:
            mds.put(req, equity, equity.as_of_date)
    return mds


def _single(right, strike, exercise, qty=1.0, structure="vanilla"):
    return EquityOptionTrade(
        trade_id="X", description="t", currency="USD", notional=0.0,
        underlier="XYZ", contract_multiplier=1.0, structure=structure,
        legs=[OptionLeg(right, strike, EXPIRY, exercise, qty)],
    )


def _price(trade, ctx, curve_data, equity):
    return get_pricer(trade).price(trade, _resolve(trade, ctx, curve_data, equity), ctx)


# --------------------------------------------------------------------------- #
def test_european_put_call_parity(ctx, curve_data, equity):
    """C - P = S e^{-qT} - K e^{-rT} for European options (model-independent)."""
    K = 200.0
    call = _price(_single(OptionRight.CALL, K, ExerciseStyle.EUROPEAN), ctx, curve_data, equity)
    put = _price(_single(OptionRight.PUT, K, ExerciseStyle.EUROPEAN), ctx, curve_data, equity)

    ql.Settings.instance().evaluationDate = cv.to_ql_date(VDATE)
    ts = build_ois_curve(curve_data).currentLink()
    T = ql.Actual365Fixed().yearFraction(cv.to_ql_date(VDATE), cv.to_ql_date(EXPIRY))
    df = ts.discount(cv.to_ql_date(EXPIRY))
    lhs = call.pv - put.pv
    rhs = SPOT * math.exp(-QDIV * T) - K * df
    assert abs(lhs - rhs) < 0.05, f"parity off: {lhs:.4f} vs {rhs:.4f}"


def test_american_ge_european_put(ctx, curve_data, equity):
    amer = _price(_single(OptionRight.PUT, 210.0, ExerciseStyle.AMERICAN), ctx, curve_data, equity)
    euro = _price(_single(OptionRight.PUT, 210.0, ExerciseStyle.EUROPEAN), ctx, curve_data, equity)
    assert amer.pv >= euro.pv - 1e-6


def test_option_delta_signs(ctx, curve_data, equity):
    call = _price(_single(OptionRight.CALL, 200.0, ExerciseStyle.EUROPEAN), ctx, curve_data, equity)
    put = _price(_single(OptionRight.PUT, 200.0, ExerciseStyle.EUROPEAN), ctx, curve_data, equity)
    assert call.measures["delta_shares"] > 0
    assert put.measures["delta_shares"] < 0
    assert call.measures["vega_1volpt"] > 0     # long option => positive vega


def test_value_monotonic_in_vol(ctx, curve_data):
    def surf(vol):
        days, strikes, ivs = [], [], []
        for d in (30, 180, 400):
            for k in range(50, 401, 10):
                days.append(d)
                strikes.append(float(k))
                ivs.append(vol)
        return EquityMarketData("XYZ", "USD", SPOT, QDIV, VDATE,
                                EquityVolSurface("XYZ", tuple(days), tuple(strikes), tuple(ivs), VDATE))
    t = _single(OptionRight.CALL, 200.0, ExerciseStyle.EUROPEAN)
    lo = _price(t, ctx, curve_data, surf(0.20)).pv
    hi = _price(t, ctx, curve_data, surf(0.40)).pv
    assert hi > lo


def test_call_spread_value_bounds(ctx, curve_data, equity):
    """Long 190 / short 210 call spread: 0 < value < width (20) * multiplier."""
    spread = EquityOptionTrade(
        trade_id="CS", description="call spread", currency="USD", notional=0.0,
        underlier="XYZ", contract_multiplier=1.0, structure="call_spread",
        legs=[OptionLeg(OptionRight.CALL, 190.0, EXPIRY, ExerciseStyle.EUROPEAN, 1.0),
              OptionLeg(OptionRight.CALL, 210.0, EXPIRY, ExerciseStyle.EUROPEAN, -1.0)],
    )
    r = _price(spread, ctx, curve_data, equity)
    assert 0 < r.pv < 20.0
    assert len(r.leg_pvs) == 2


def test_straddle_is_call_plus_put(ctx, curve_data, equity):
    straddle = EquityOptionTrade(
        trade_id="ST", description="straddle", currency="USD", notional=0.0,
        underlier="XYZ", contract_multiplier=1.0, structure="straddle",
        legs=[OptionLeg(OptionRight.CALL, 200.0, EXPIRY, ExerciseStyle.EUROPEAN, 1.0),
              OptionLeg(OptionRight.PUT, 200.0, EXPIRY, ExerciseStyle.EUROPEAN, 1.0)],
    )
    r = _price(straddle, ctx, curve_data, equity)
    c = _price(_single(OptionRight.CALL, 200.0, ExerciseStyle.EUROPEAN), ctx, curve_data, equity)
    p = _price(_single(OptionRight.PUT, 200.0, ExerciseStyle.EUROPEAN), ctx, curve_data, equity)
    assert r.pv == pytest.approx(c.pv + p.pv, rel=1e-9)
    assert r.measures["vega_1volpt"] > 0
