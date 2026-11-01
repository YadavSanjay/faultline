"""Bond pricer correctness on mock market data (no CSV files).

Isolated logic checks for the corporate and Treasury bond pricers, driven by
hand-built domain market data — demonstrating mockability and locking in the
price/yield/spread relationships.
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import PAR_RATES, PAR_TENORS, VALUATION_DATE
from src.pricer.input.bonds.instruments import CorporateBondTrade, TreasuryBondTrade
from src.pricer.input.common.conventions import IndexConvention
from src.pricer.input.common.enums import MarketDataKind
from src.pricer.input.common.market_data import (
    CreditSpreadData,
    MarketDataSet,
    ParOISCurveData,
    TreasuryCurveData,
)
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.ql.bonds.pricer_factory import get_pricer

NOTIONAL = 50_000_000


@pytest.fixture
def ctx():
    return ValuationContext(valuation_date=VALUATION_DATE)


@pytest.fixture
def sofr_curve():
    return ParOISCurveData("USD", IndexConvention.usd_sofr(), VALUATION_DATE,
                           PAR_TENORS, PAR_RATES, source="unit-test")


@pytest.fixture
def treasury_curve():
    return TreasuryCurveData("USD", VALUATION_DATE,
                             ("2Y", "5Y", "10Y", "30Y"),
                             (0.0398, 0.0413, 0.0445, 0.0499), source="unit-test")


def _resolve(trade, ctx, *, ois=None, treasury=None, credit=None) -> MarketDataSet:
    mds = MarketDataSet(valuation_date=ctx.valuation_date)
    for req in trade.market_data_requirements(ctx).requirements:
        if req.kind == MarketDataKind.OIS_CURVE:
            mds.put(req, ois, ois.as_of_date)
        elif req.kind == MarketDataKind.TREASURY_CURVE:
            mds.put(req, treasury, treasury.as_of_date)
        elif req.kind == MarketDataKind.CREDIT_SPREAD:
            mds.put(req, credit, credit.as_of_date)
    return mds


def _corp(coupon, maturity, issuer="AAPL"):
    return CorporateBondTrade(
        trade_id="C", description="corp", currency="USD", notional=NOTIONAL,
        issuer=issuer, coupon=coupon, issue_date=date(2025, 5, 12),
        maturity_date=maturity,
    )


def _price(trade, mds, ctx):
    return get_pricer(trade).price(trade, mds, ctx)


# --------------------------------------------------------------------------- #
# Corporate bonds
# --------------------------------------------------------------------------- #
def test_corp_premium_when_coupon_above_yield(ctx, sofr_curve):
    # SOFR ~3.8-4.0% + 35bp credit -> yield ~4.2%; coupon 4.75% -> premium (>100)
    trade = _corp(0.0475, date(2035, 5, 12))
    credit = CreditSpreadData("AAPL", "USD", 0.0035, VALUATION_DATE)
    r = _price(trade, _resolve(trade, ctx, ois=sofr_curve, credit=credit), ctx)
    assert r.measures["clean_price"] > 100.0
    assert r.measures["mod_duration"] > 0
    assert r.measures["dv01"] > 0
    # NPV (to valuation date) ~ dirty settlement value, up to ~2d of discounting
    assert r.pv == pytest.approx(r.measures["dirty_price"] / 100.0 * NOTIONAL, rel=2e-3)
    assert r.pv < r.measures["dirty_price"] / 100.0 * NOTIONAL  # discounted to today


def test_corp_zspread_recovers_input_spread(ctx, sofr_curve):
    trade = _corp(0.0475, date(2035, 5, 12))
    credit = CreditSpreadData("AAPL", "USD", 0.0050, VALUATION_DATE)
    r = _price(trade, _resolve(trade, ctx, ois=sofr_curve, credit=credit), ctx)
    # zSpread (semi-comp) recovers the input continuous zero spread to within ~3bp
    assert abs(r.measures["z_spread_bp"] - 50.0) < 3.0


def test_corp_wider_spread_lowers_price(ctx, sofr_curve):
    trade = _corp(0.0475, date(2035, 5, 12))
    tight = _price(trade, _resolve(trade, ctx, ois=sofr_curve,
                                   credit=CreditSpreadData("AAPL", "USD", 0.0035, VALUATION_DATE)), ctx)
    wide = _price(trade, _resolve(trade, ctx, ois=sofr_curve,
                                  credit=CreditSpreadData("AAPL", "USD", 0.0150, VALUATION_DATE)), ctx)
    assert wide.pv < tight.pv
    assert wide.measures["z_spread_bp"] > tight.measures["z_spread_bp"]


def test_corp_fallback_flag_propagates(ctx, sofr_curve):
    trade = _corp(0.0475, date(2035, 5, 12))
    credit = CreditSpreadData("AAPL", "USD", 0.0035, VALUATION_DATE, from_cds=False, source="config-fallback")
    r = _price(trade, _resolve(trade, ctx, ois=sofr_curve, credit=credit), ctx)
    assert r.diagnostics["credit_spread_from_cds"] == "False"


# --------------------------------------------------------------------------- #
# Treasury bonds
# --------------------------------------------------------------------------- #
def test_ust_par_bond_prices_at_par(ctx, treasury_curve):
    # coupon == the curve's 10Y par yield -> clean price ~100
    trade = TreasuryBondTrade(
        trade_id="T", description="UST 10Y par", currency="USD", notional=NOTIONAL,
        term="10Y", coupon=0.0445, issue_date=VALUATION_DATE, maturity_date=date(2036, 5, 29),
    )
    r = _price(trade, _resolve(trade, ctx, treasury=treasury_curve), ctx)
    assert r.measures["clean_price"] == pytest.approx(100.0, abs=0.5)
    assert r.measures["mod_duration"] > 0
    assert r.measures["ytm"] == pytest.approx(0.0445, abs=5e-4)


def test_ust_discount_when_coupon_below_yield(ctx, treasury_curve):
    trade = TreasuryBondTrade(
        trade_id="T", description="UST low cpn", currency="USD", notional=NOTIONAL,
        term="10Y", coupon=0.030, issue_date=VALUATION_DATE, maturity_date=date(2036, 5, 29),
    )
    r = _price(trade, _resolve(trade, ctx, treasury=treasury_curve), ctx)
    assert r.measures["clean_price"] < 100.0
