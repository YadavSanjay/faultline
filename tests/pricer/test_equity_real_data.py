"""Real-market-data integration & validation tests for the equity-option book (#7-14).

Drives the real option chains through the actual loader and asserts:
  1. Cross-source consistency — the chain-recovered NVDA spot matches the
     independent daily close; implied dividend yields are realistic.
  2. The recovered vol surface shows put-wing skew.
  3. Greeks have the right signs for each structure.
  4. Golden PV regression for all 8 trades.

Equities value at the chain snapshot date 2026-06-17.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.equity_adapter import EquityMarketDataAdapter
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.ql.factory import get_pricer

VDATE = date(2026, 6, 17)

# Core default book @ 2026-06-17 chain snapshot (rebaselined 2026-07-27 realized risk-share cap: 38-trade book,
# scale-to-budget sizing, docs/design/portfolio_rebalance_review.md). 9 option trades: #7-14 plus the
# Part-2 deeper-OTM SPX convexity put (#35). Values carry the B1 discrete-dividend early-exercise.
# #9/#11/#12/#14 re-pinned 2026-10-06 after their roll to new expiries/strikes (portfolio roll_history).
# Re-sized 2026-10-11 (sizing re-run on 2026-10-06 data, run 6f2dc4d4): every pin scaled by its trade's
# position-size ratio (contract-count ratio for options); see golden_pvs.json rebaseline_history.
GOLDEN_PV = {
    "TRADE_7": 3_480_456.29,
    "TRADE_8": 3_700_242.60,
    "TRADE_9": 363_156.14,
    "TRADE_10": 6_292_972.25,
    "TRADE_11": 14_667_785.05,
    "TRADE_12": 1_783_523.36,
    "TRADE_13": 283_568.64,
    "TRADE_14": 1_687_518.25,
    "TRADE_35": 185_696.56,
}


@pytest.fixture(scope="module")
def root():
    return get_project_root()


@pytest.fixture(scope="module")
def config():
    return load_pricer_config()


@pytest.fixture(scope="module")
def ctx():
    return ValuationContext(valuation_date=VDATE)


@pytest.fixture(scope="module")
def adapter(config, root):
    return EquityMarketDataAdapter(config, root)


@pytest.fixture(scope="module")
def priced(config, root, ctx):
    loader = MarketDataLoader(config, root)
    out = {}
    for trade in load_portfolio(asset_class="options"):
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        out[trade.trade_id] = (trade, get_pricer(trade).price(trade, mds, ctx))
    return out


# --------------------------------------------------------------------------- #
# 1. Cross-source consistency
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_recovered_nvda_spot_matches_close(root, adapter, ctx):
    # The chain is snapshotted intraday, so its parity-recovered spot is compared with that
    # day's traded range rather than the EOD close (they legitimately differ by the session's move).
    eq, _as_of, _w = adapter.equity_market("NVDA", ctx)
    px = pd.read_csv(root / "data" / "marketdata" / "equity" / "prices_daily.csv")
    px = px[(px.ticker == "NVDA") & (px.date <= "2026-06-17")].sort_values("date")
    day = px.iloc[-1]
    lo, hi = float(day["low"]), float(day["high"])
    assert lo * 0.995 <= eq.spot <= hi * 1.005, f"recovered {eq.spot:.2f} outside day range {lo:.2f}-{hi:.2f}"


@pytest.mark.needs_market_data
@pytest.mark.parametrize("name", ["NVDA", "AAPL", "MSFT", "META", "TSLA", "QQQ", "SPX"])
def test_implied_dividend_yields_realistic(adapter, ctx, name):
    eq, _as_of, _w = adapter.equity_market(name, ctx)
    assert -0.005 < eq.dividend_yield < 0.03, f"{name} q={eq.dividend_yield:.4f}"
    assert eq.spot > 0


@pytest.mark.needs_market_data
def test_vol_surface_has_put_skew(adapter, ctx):
    """Index downside strikes carry higher IV than ATM (equity put skew)."""
    eq, _as_of, _w = adapter.equity_market("SPX", ctx)
    t = 0.25
    atm = eq.vol_surface.implied_vol(t, eq.spot)
    down = eq.vol_surface.implied_vol(t, eq.spot * 0.85)
    assert down > atm, f"no skew: down {down:.4f} <= atm {atm:.4f}"


# --------------------------------------------------------------------------- #
# B1 — discrete dividends
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_dividend_schedule_payers_vs_nonpayers(adapter, ctx):
    """AAPL (a payer) projects a forward ex-dividend schedule; TSLA (non-payer, absent from
    the dividends feed) and SPX (index, no per-share dividend feed) return none."""
    aapl, _a, _w = adapter.equity_market("AAPL", ctx)
    assert len(aapl.dividends) >= 2                      # quarterly, projected over ~2y
    assert all(d > ctx.valuation_date for d, _amt in aapl.dividends)   # future ex-dates
    assert all(amt > 0 for _d, amt in aapl.dividends)
    for name in ("TSLA", "SPX"):
        eq, _a2, _w2 = adapter.equity_market(name, ctx)
        assert eq.dividends == ()


@pytest.mark.needs_market_data
def test_american_call_uses_discrete_dividends(priced):
    _, r = priced["TRADE_7"]                              # AAPL American LEAPS call
    assert r.measures["discrete_dividend_legs"] >= 1
    assert "discrete" in r.diagnostics["dividends"]


@pytest.mark.needs_market_data
def test_european_legs_not_affected_by_discrete_dividends(priced):
    # SPX legs are European -> continuous yield, never the discrete-dividend path.
    for tid in ("TRADE_12", "TRADE_13"):
        _, r = priced[tid]
        assert r.measures["discrete_dividend_legs"] == 0


@pytest.mark.needs_market_data
def test_black_variance_surface_builds_arbitrage_free(adapter, ctx):
    """B2: the BlackVarianceSurface builds for a real underlier, is calendar-arbitrage-free
    (total variance non-decreasing in time at each strike), and reproduces the chain IV at a
    leg-strike node."""
    import QuantLib as ql
    from src.pricer.ql.common import conventions as cv
    from src.pricer.ql.common.vol_builder import build_equity_black_variance_surface

    eq, _a, _w = adapter.equity_market("MSFT", ctx)
    today = cv.to_ql_date(VDATE)
    ql.Settings.instance().evaluationDate = today
    cal = ql.UnitedStates(ql.UnitedStates.NYSE)
    dc = ql.Actual365Fixed()
    K = 400.0
    surf = build_equity_black_variance_surface(eq.vol_surface, eq.spot, today, cal, dc, extra_strikes=(K,))
    assert surf is not None
    # arbitrage-free: total variance sigma^2*t non-decreasing across a sweep of expiries
    prev = -1.0
    for months in (1, 2, 3, 6, 9):
        d = today + ql.Period(months, ql.Months)
        var = surf.blackVariance(d, K)
        assert var >= prev - 1e-10, f"variance decreased at {months}M"
        prev = var
    # faithful at the (leg strike, chain expiry) node
    d90 = today + ql.Period(90, ql.Days)
    assert abs(surf.blackVol(d90, K) - eq.vol_surface.implied_vol(90 / 365.0, K)) < 0.02


@pytest.mark.needs_market_data
def test_vol_surface_grid_in_lineage(priced):
    """The option Explain payload carries the vol-surface grid (strike x expiry, IV%) with the
    legs marked — the heatmap the UI renders."""
    from src.pricer.config import get_project_root, load_pricer_config
    from src.pricer.engine import PricingEngine
    from src.pricer.lineage import build_explain

    eng = PricingEngine(load_pricer_config(), get_project_root())
    trade = priced["TRADE_10"][0]                       # META put spread (2 legs)
    exp = build_explain(eng, trade, ValuationContext(valuation_date=VDATE))
    vs = exp["intermediates"]["vol_surface"]
    assert vs and len(vs["strikes"]) >= 2 and len(vs["expiries_days"]) >= 2
    assert len(vs["iv_pct"]) == len(vs["strikes"])
    assert len(vs["leg_points"]) == len(trade.legs)


def test_discrete_scaling_preserves_forward_for_european():
    """The forward-preserving scaling: a EUROPEAN option priced with the chain-implied
    continuous yield equals the same option priced with q=0 + the scaled discrete
    dividends. This proves B1 only changes American early-exercise, not the forward."""
    import QuantLib as ql
    from src.pricer.ql.common import conventions as cv

    today = cv.to_ql_date(VDATE)
    ql.Settings.instance().evaluationDate = today
    dc = ql.Actual365Fixed()
    cal = ql.UnitedStates(ql.UnitedStates.NYSE)
    S, q, r, sigma, K = 320.0, 0.006, 0.042, 0.28, 320.0
    expiry = today + ql.Period(9, ql.Months)
    T = dc.yearFraction(today, expiry)
    disc = ql.YieldTermStructureHandle(ql.FlatForward(today, r, dc))
    vol = ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today, cal, sigma, dc))
    spot = ql.QuoteHandle(ql.SimpleQuote(S))
    payoff = ql.PlainVanillaPayoff(ql.Option.Call, K)
    exercise = ql.EuropeanExercise(expiry)

    # continuous-yield European
    cont = ql.VanillaOption(payoff, exercise)
    cont.setPricingEngine(ql.AnalyticEuropeanEngine(
        ql.BlackScholesMertonProcess(spot, ql.YieldTermStructureHandle(ql.FlatForward(today, q, dc)), disc, vol)))

    # discrete divs (two ex-dates) scaled to S*(1-exp(-qT)); q=0
    import math
    div_dates = [today + ql.Period(2, ql.Months), today + ql.Period(5, ql.Months)]
    raw = [1.0, 1.0]
    raw_pv = sum(a * disc.discount(d) for a, d in zip(raw, div_dates, strict=True))
    scale = S * (1.0 - math.exp(-q * T)) / raw_pv
    sched = ql.DividendSchedule()
    for a, d in zip(raw, div_dates, strict=True):
        sched.push_back(ql.FixedDividend(a * scale, d))
    disc_opt = ql.VanillaOption(payoff, exercise)
    disc_opt.setPricingEngine(ql.FdBlackScholesVanillaEngine(
        ql.BlackScholesMertonProcess(spot, ql.YieldTermStructureHandle(ql.FlatForward(today, 0.0, dc)), disc, vol),
        sched, 200, 200))
    assert disc_opt.NPV() == pytest.approx(cont.NPV(), rel=3e-3)


# --------------------------------------------------------------------------- #
# 2. Greek signs per structure
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_protective_put_negative_delta(priced):
    _, r = priced["TRADE_8"]                 # NVDA long put
    assert r.measures["delta_shares"] < 0
    assert r.measures["vega_1volpt"] > 0     # long option => long vega


@pytest.mark.needs_market_data
def test_leaps_call_positive_delta(priced):
    _, r = priced["TRADE_7"]                  # AAPL long call
    assert r.measures["delta_shares"] > 0


@pytest.mark.needs_market_data
def test_collar_is_short_delta(priced):
    _, r = priced["TRADE_14"]                 # long put + short call => negative delta
    assert r.measures["delta_shares"] < 0


@pytest.mark.needs_market_data
def test_straddle_long_vega(priced):
    _, r = priced["TRADE_11"]                 # TSLA straddle
    assert r.measures["vega_1volpt"] > 0


# --------------------------------------------------------------------------- #
# 3. Sanity + golden PVs
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_all_equity_priced_finite(priced):
    for _t, r in priced.values():
        assert r.pv == r.pv and abs(r.pv) > 0   # not NaN, non-zero


@pytest.mark.needs_market_data
@pytest.mark.parametrize("tid", list(GOLDEN_PV))
def test_golden_pv_real_data(priced, tid):
    _, r = priced[tid]
    assert r.pv == pytest.approx(GOLDEN_PV[tid], rel=2e-3), f"{tid}: {r.pv:,.2f}"
