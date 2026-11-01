"""Cash-equity pricer tests (trades #1-6): mocked logic + real-data marks.

Cash equity is mark-to-market (value = quantity x spot), so the checks are the
sign/linearity invariants and that marks match the independent daily close.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.cash_equity.instruments import CashEquityTrade
from src.pricer.input.common.enums import MarketDataKind
from src.pricer.input.common.market_data import EquitySpotData, MarketDataSet
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.ql.factory import get_pricer

VDATE = date(2026, 5, 29)

# Core default book @ 2026-05-29 (rebaselined 2026-07-27 risk-share cap: 38-trade book, own-factor CEG/VST/GOOGL +
# INTC/AMD dispersion pair, docs/design/portfolio_rebalance_review.md). 12 cash-equity names: AI-hardware
# longs #1-6, Part-2 diversifiers CEG/VST/GOOGL/GLD (#31-34), and the N5 INTC/AMD semis pair (#37-38).
# Re-sized 2026-10-11 (sizing re-run on 2026-10-06 data, run 6f2dc4d4): every pin scaled by its trade's
# position-size ratio (contract-count ratio for options); see golden_pvs.json rebaseline_history.
GOLDEN_PV = {
    "TRADE_1": 93_004_213.77,
    "TRADE_2": 76_199_328.77,
    "TRADE_3": 67_482_819.76,
    "TRADE_4": 47_400_336.00,
    "TRADE_5": 112_462_454.45,
    "TRADE_6": -115_279_050.35,
    "TRADE_31": 46_584_135.25,
    "TRADE_32": 55_156_612.35,
    "TRADE_33": 96_786_640.23,
    "TRADE_34": 68_682_144.16,
    "TRADE_37": 66_418_527.70,
    "TRADE_38": -37_428_602.43,
}


@pytest.fixture
def ctx():
    return ValuationContext(valuation_date=VDATE)


# --------------------------------------------------------------------------- #
# Mocked logic
# --------------------------------------------------------------------------- #
def _price_mock(trade, ctx, spot):
    mds = MarketDataSet(valuation_date=ctx.valuation_date)
    eq = EquitySpotData(trade.ticker, "USD", spot, ctx.valuation_date, dividend_yield=0.005)
    for req in trade.market_data_requirements(ctx).requirements:
        assert req.kind == MarketDataKind.EQUITY_SPOT
        mds.put(req, eq, ctx.valuation_date)
    return get_pricer(trade).price(trade, mds, ctx)


def test_long_position_value(ctx):
    t = CashEquityTrade(trade_id="L", description="long", currency="USD",
                        notional=0.0, ticker="NVDA", quantity=1000)
    r = _price_mock(t, ctx, spot=200.0)
    assert r.pv == pytest.approx(200_000.0)
    assert r.measures["cash_delta"] == pytest.approx(200_000.0)
    assert r.measures["delta_shares"] == 1000
    assert r.measures["gross_notional"] == pytest.approx(200_000.0)


def test_short_position_negative_value(ctx):
    t = CashEquityTrade(trade_id="S", description="short", currency="USD",
                        notional=0.0, ticker="SMH", quantity=-500)
    r = _price_mock(t, ctx, spot=600.0)
    assert r.pv == pytest.approx(-300_000.0)
    assert r.measures["gross_notional"] == pytest.approx(300_000.0)  # gross is positive
    assert r.diagnostics["direction"] == "short"


# --------------------------------------------------------------------------- #
# Real data
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def priced():
    root = get_project_root()
    config = load_pricer_config()
    ctx = ValuationContext(valuation_date=VDATE)
    loader = MarketDataLoader(config, root)
    out = {}
    for trade in load_portfolio(asset_class="equity"):
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        out[trade.trade_id] = (trade, get_pricer(trade).price(trade, mds, ctx))
    return out


@pytest.mark.needs_market_data
def test_spot_matches_daily_close(priced):
    root = get_project_root()
    px = pd.read_csv(root / "data" / "marketdata" / "equity" / "prices_daily.csv")
    for trade, r in priced.values():
        s = px[(px.ticker == trade.ticker) & (px.date <= "2026-05-29")].sort_values("date")
        close = float(s.iloc[-1]["close"])
        assert r.measures["spot"] == pytest.approx(close, rel=1e-9)
        assert r.pv == pytest.approx(trade.quantity * close, rel=1e-9)


@pytest.mark.needs_market_data
def test_smh_is_short(priced):
    _, r = priced["TRADE_6"]
    assert r.pv < 0


@pytest.mark.needs_market_data
@pytest.mark.parametrize("tid", list(GOLDEN_PV))
def test_golden_pv_real_data(priced, tid):
    _, r = priced[tid]
    assert r.pv == pytest.approx(GOLDEN_PV[tid], rel=1e-6)
