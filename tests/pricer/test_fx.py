"""FX pricer tests (trades #26-27): mocked logic + real-data marks.

Validates the discount-factor FX forward (CIP self-consistency, sign conventions,
FX delta) and the fixed-for-fixed cross-currency swap (FX MtM direction, delta),
plus golden PVs from the real FX feeds.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.conventions import IndexConvention
from src.pricer.input.common.enums import FxDirection, MarketDataKind
from src.pricer.input.common.market_data import FxData, MarketDataSet, ParOISCurveData
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.input.fx.instruments import CrossCurrencySwapTrade, FxForwardTrade
from src.pricer.loader.fx_adapter import FxMarketDataAdapter
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.ql.factory import get_pricer

VDATE = date(2026, 5, 29)

# Core default book @ 2026-05-29 (rebaselined 2026-07-27 realized risk-share cap: 38-trade book, scale-to-budget
# sizing, docs/design/portfolio_rebalance_review.md). TRADE_26 USD/TWD CCS marks off the B3 CBC NDF spot.
# TRADE_27 re-pinned 2026-10-06 after its roll to a 2027-01-08 forward struck at 156.94 (portfolio roll_history).
# Re-sized 2026-10-11 (sizing re-run on 2026-10-06 data, run 6f2dc4d4): every pin scaled by its trade's
# position-size ratio (contract-count ratio for options); see golden_pvs.json rebaseline_history.
GOLDEN_PV = {
    "TRADE_26": 1_686_244.26,
    "TRADE_27": -417_315.10,
}


@pytest.fixture
def ctx():
    return ValuationContext(valuation_date=VDATE)


@pytest.fixture
def usd_curve():
    return ParOISCurveData("USD", IndexConvention.usd_sofr(), VDATE,
                           ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y"), (0.040,) * 6, source="unit-test")


def _fx(foreign="JPY", spot=150.0, rate=0.007):
    return FxData(foreign, spot, rate, VDATE, source="test")


def _resolve(trade, ctx, usd_curve, fx) -> MarketDataSet:
    mds = MarketDataSet(valuation_date=ctx.valuation_date)
    for req in trade.market_data_requirements(ctx).requirements:
        if req.kind == MarketDataKind.OIS_CURVE:
            mds.put(req, usd_curve, usd_curve.as_of_date)
        elif req.kind == MarketDataKind.FX:
            mds.put(req, fx, fx.as_of_date)
    return mds


def _price(trade, ctx, usd_curve, fx):
    return get_pricer(trade).price(trade, _resolve(trade, ctx, usd_curve, fx), ctx)


def _fwd(strike, direction=FxDirection.LONG_USD, foreign="JPY"):
    return FxForwardTrade(
        trade_id="F", description="fwd", currency="USD", notional=10_000_000,
        foreign_ccy=foreign, usd_notional=10_000_000, strike=strike,
        maturity_date=date(2026, 8, 31), direction=direction,
    )


# --------------------------------------------------------------------------- #
# FX forward
# --------------------------------------------------------------------------- #
def test_forward_struck_at_fair_is_zero(ctx, usd_curve):
    fx = _fx()
    fair = _price(_fwd(strike=150.0), ctx, usd_curve, fx).measures["fair_forward"]
    r = _price(_fwd(strike=fair), ctx, usd_curve, fx)
    assert abs(r.pv) < 1.0


def test_xccy_basis_hook(ctx, usd_curve):
    """C2: the xccy-basis hook adds to the foreign discount rate and flows to the mark /
    fair forward; it is 0 by default (no free basis feed) so nothing changes then."""
    base = _fx(foreign="TWD", spot=31.0, rate=0.02)
    bumped = FxData("TWD", 31.0, 0.02, VDATE, source="test", xccy_basis_bp=50.0)
    assert base.effective_foreign_rate == pytest.approx(0.02)
    assert bumped.effective_foreign_rate == pytest.approx(0.025)
    r0 = _price(_fwd(strike=31.0, foreign="TWD"), ctx, usd_curve, base)
    r1 = _price(_fwd(strike=31.0, foreign="TWD"), ctx, usd_curve, bumped)
    assert r0.measures["xccy_basis_bp"] == 0.0 and r1.measures["xccy_basis_bp"] == 50.0
    assert r0.measures["fair_forward"] != r1.measures["fair_forward"]


def test_forward_fair_below_spot_when_usd_rate_higher(ctx, usd_curve):
    # USD 4% > JPY 0.7% -> USD at a forward discount in JPY terms -> fair fwd < spot
    r = _price(_fwd(strike=150.0), ctx, usd_curve, _fx(spot=150.0, rate=0.007))
    assert r.measures["fair_forward"] < r.measures["spot"]


def test_forward_long_short_opposite_sign(ctx, usd_curve):
    fx = _fx()
    lng = _price(_fwd(strike=148.0, direction=FxDirection.LONG_USD), ctx, usd_curve, fx)
    sht = _price(_fwd(strike=148.0, direction=FxDirection.SHORT_USD), ctx, usd_curve, fx)
    assert lng.pv == pytest.approx(-sht.pv, rel=1e-9)


def test_forward_fx_delta_long_usd_positive(ctx, usd_curve):
    # long USD gains when the foreign weakens (+1% foreign-per-USD)
    r = _price(_fwd(strike=148.0), ctx, usd_curve, _fx())
    assert r.measures["fx_delta_1pct"] > 0


# --------------------------------------------------------------------------- #
# Cross-currency swap
# --------------------------------------------------------------------------- #
def _ccs(contract_fx, direction=FxDirection.LONG_USD):
    return CrossCurrencySwapTrade(
        trade_id="X", description="ccs", currency="USD", notional=10_000_000,
        foreign_ccy="TWD", usd_notional=10_000_000, contract_fx=contract_fx,
        usd_fixed_rate=0.038, foreign_fixed_rate=0.020,
        start_date=date(2025, 5, 29), maturity_date=date(2030, 5, 29),
        direction=direction,
    )


def test_ccs_gains_when_foreign_weakens(ctx, usd_curve):
    fx = _fx(foreign="TWD", spot=31.0, rate=0.02)
    # contracted at 30 (foreign stronger) -> foreign since weakened to 31 -> LONG_USD gains
    weak = _price(_ccs(contract_fx=30.0), ctx, usd_curve, fx)
    # contracted at 32 (foreign weaker than now) -> LONG_USD loses
    strong = _price(_ccs(contract_fx=32.0), ctx, usd_curve, fx)
    assert weak.pv > 0
    assert strong.pv < 0


def test_ccs_long_short_opposite_sign(ctx, usd_curve):
    fx = _fx(foreign="TWD", spot=31.0, rate=0.02)
    lng = _price(_ccs(30.0, FxDirection.LONG_USD), ctx, usd_curve, fx)
    sht = _price(_ccs(30.0, FxDirection.SHORT_USD), ctx, usd_curve, fx)
    assert lng.pv == pytest.approx(-sht.pv, rel=1e-9)


def test_ccs_fx_delta_long_usd_positive(ctx, usd_curve):
    r = _price(_ccs(30.0), ctx, usd_curve, _fx(foreign="TWD", spot=31.0, rate=0.02))
    assert r.measures["fx_delta_1pct"] > 0


# --------------------------------------------------------------------------- #
# Real data
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_adapter_spots_realistic(ctx):
    adapter = FxMarketDataAdapter(load_pricer_config(), get_project_root())
    jpy, _a, _w = adapter.fx("JPY", ctx)
    twd, _a2, _w2 = adapter.fx("TWD", ctx)
    assert 100 < jpy.spot < 200 and 0.0 < jpy.foreign_rate < 0.05
    assert 25 < twd.spot < 40 and twd.foreign_rate == pytest.approx(0.020)


@pytest.fixture(scope="module")
def priced():
    root = get_project_root()
    config = load_pricer_config()
    ctx = ValuationContext(valuation_date=VDATE)
    loader = MarketDataLoader(config, root)
    out = {}
    for trade in load_portfolio(asset_class="fx"):
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        out[trade.trade_id] = (trade, get_pricer(trade).price(trade, mds, ctx))
    return out


@pytest.mark.needs_market_data
def test_twd_ccs_positive_hedge(priced):
    # contract 30 vs current ~31.4 -> long-USD hedge in the money
    assert priced["TRADE_26"][1].pv > 0


@pytest.mark.needs_market_data
@pytest.mark.parametrize("tid", list(GOLDEN_PV))
def test_golden_pv_real_data(priced, tid):
    _, r = priced[tid]
    assert r.pv == pytest.approx(GOLDEN_PV[tid], rel=2e-3), f"{tid}: {r.pv:,.2f}"
