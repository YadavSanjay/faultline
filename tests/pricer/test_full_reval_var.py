"""P2 full-revaluation historical VaR: granular bumps, factor history, and the engine."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.market_data import EquitySpotData
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk import bumps
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.risk.historical_var import FullRevalVarEngine

VDATE = date(2026, 5, 29)


# --------------------------------------------------------------------------- #
# Granular bump transforms (no pricing)
# --------------------------------------------------------------------------- #
def test_shift_ir_curve_interpolates(curve_data):
    tf = bumps.shift_ir_curve((1.0, 10.0), (0.0010, 0.0020))   # 1Y +10bp, 10Y +20bp
    out = tf(curve_data)
    base = dict(zip(curve_data.tenors, curve_data.par_rates, strict=True))
    new = dict(zip(out.tenors, out.par_rates, strict=True))
    assert new["1Y"] == pytest.approx(base["1Y"] + 0.0010, abs=1e-12)
    assert new["10Y"] == pytest.approx(base["10Y"] + 0.0020, abs=1e-12)
    exp5 = 0.0010 + (5.0 - 1.0) / (10.0 - 1.0) * 0.0010        # linear interp at 5Y
    assert new["5Y"] == pytest.approx(base["5Y"] + exp5, abs=1e-12)


def test_scale_equity_named_per_name_and_default():
    tf = bumps.scale_equity_named({"NVDA": 0.05}, default=-0.10)
    nvda = EquitySpotData("NVDA", "USD", 100.0, VDATE)
    other = EquitySpotData("TSM", "USD", 50.0, VDATE)
    assert tf(nvda).spot == pytest.approx(105.0)        # named +5%
    assert tf(other).spot == pytest.approx(45.0)        # default -10%


# --------------------------------------------------------------------------- #
# Factor history
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_factor_history_aligned_shocks():
    fh = FactorHistory(get_project_root(), load_pricer_config())
    shocks = fh.day_shocks(60)
    assert len(shocks) > 0
    s = shocks[-1]
    assert len(s.transforms()) == 9              # +FX rho (GAP 5) +vol term/skew (GAP 4)
    assert len(s.ir_points_years) == len(s.ir_points_shift) >= 2
    assert "JPY" in s.fx_by_ccy and "TWD" in s.fx_by_ccy
    assert "JPY" in s.fxrate_by_ccy


@pytest.mark.needs_market_data
def test_credit_factor_uses_clean_oas():
    """GAP 1 closed: the credit factor is the clean FRED BofA IG OAS, not the noisy proxy."""
    fh = FactorHistory(get_project_root(), load_pricer_config())
    oas = fh._credit_oas()
    assert oas is not None and not oas.empty             # BofA IG OAS series wired in
    assert oas.abs().max() < 0.01                        # clean daily moves (< 100bp, decimal)


def test_var_credit_config_points_to_baml():
    factors = load_pricer_config()["risk"]["var"]["factors"]
    ig = factors["CREDIT_IG"]
    assert ig["column"] == "BAMLC0A0CM" and ig.get("move_unit_bp") is True
    assert ig["maps_to"] == "CS01:IG"
    # Tier 3.9 full split: high-yield CS01 maps to the ICE BofA HY OAS series.
    assert factors["CREDIT_HY"]["column"] == "BAMLH0A0HYM2" and factors["CREDIT_HY"]["maps_to"] == "CS01:HY"


@pytest.mark.needs_market_data
def test_option_underliers_have_own_history():
    """GAP 2: option underliers now carry their own spot history (not the XLK proxy)."""
    fh = FactorHistory(get_project_root(), load_pricer_config())
    panel = fh.aligned_panel(120)
    for name in ("AAPL", "MSFT", "META", "TSLA", "QQQ", "SPX"):
        assert f"eq:{name}" in panel.columns
    # the VaR config maps each to its own ticker, not an XLK proxy
    for name in ("AAPL", "MSFT", "TSLA", "SPX"):
        f = load_pricer_config()["risk"]["var"]["factors"][f"EQ_{name}"]
        assert f["ticker"] == name and "proxy" not in f


# --------------------------------------------------------------------------- #
# Full-reval VaR engine (small book + short lookback for speed)
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_full_reval_var_small_book():
    cfg = load_pricer_config()
    root = get_project_root()
    ve = FullRevalVarEngine(PricingEngine(cfg, root), cfg, root)
    ctx = ValuationContext(valuation_date=VDATE)
    trades = load_portfolio(trade_id="TRADE_1") + load_portfolio(trade_id="TRADE_22")
    r = ve.historical_var(trades, ctx, lookback=60)

    assert r.n_scenarios > 10
    assert r.method.startswith("full_revaluation")
    assert r.var["0.99"] >= r.var["0.95"] > 0           # monotone in confidence
    assert r.es["0.975"] >= r.var["0.975"]              # ES never below VaR
    assert r.worst_day and r.worst_pnl < 0             # worst day is a loss
    assert r.base_pv != 0.0


@pytest.mark.needs_market_data
def test_full_reval_var_parallel_matches_sequential():
    """workers>1 fans out over trade chunks and must reproduce the sequential result."""
    cfg = load_pricer_config()
    root = get_project_root()
    ctx = ValuationContext(valuation_date=VDATE)
    trades = load_portfolio(trade_id="TRADE_1") + load_portfolio(trade_id="TRADE_22")
    seq = FullRevalVarEngine(PricingEngine(cfg, root), cfg, root).historical_var(
        trades, ctx, lookback=40, workers=1)
    par = FullRevalVarEngine(PricingEngine(cfg, root), cfg, root).historical_var(
        trades, ctx, lookback=40, workers=2)
    assert par.n_scenarios == seq.n_scenarios
    assert par.base_pv == pytest.approx(seq.base_pv, rel=1e-9)
    for lvl in ("0.95", "0.975", "0.99"):
        assert par.var[lvl] == pytest.approx(seq.var[lvl], rel=1e-9)
        assert par.es[lvl] == pytest.approx(seq.es[lvl], rel=1e-9)


@pytest.mark.needs_market_data
def test_trade_pnl_history_columns_sum_to_the_book_pnl_vector():
    """The per-trade history keeps what historical_var sums: same days, same repricing, same scaling."""
    cfg = load_pricer_config()
    root = get_project_root()
    ctx = ValuationContext(valuation_date=VDATE)
    trades = load_portfolio(trade_id="TRADE_1") + load_portfolio(trade_id="TRADE_22")
    ve = FullRevalVarEngine(PricingEngine(cfg, root), cfg, root)
    book = ve.historical_var(trades, ctx, lookback=40)
    hist = ve.trade_pnl_history(trades, ctx, lookback=40)
    assert hist.trade_ids == ["TRADE_1", "TRADE_22"]
    assert hist.dates == book.dates
    assert hist.matrix.shape == (len(book.dates), 2)
    assert list(hist.matrix.sum(axis=1)) == pytest.approx(book.pnl_vector, rel=1e-12, abs=1e-6)
    par = FullRevalVarEngine(PricingEngine(cfg, root), cfg, root).trade_pnl_history(
        trades, ctx, lookback=40, workers=2)
    assert par.trade_ids == hist.trade_ids
    assert par.matrix == pytest.approx(hist.matrix, rel=1e-9, abs=1e-6)
