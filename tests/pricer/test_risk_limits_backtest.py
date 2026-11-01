"""Risk limit monitoring + VaR backtest (item 7)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk.backtest import VarBacktester, _basel_zone, _kupiec_lr, backtest_pnl
from src.pricer.risk.limits import LimitMonitor
from src.pricer.risk.sensitivities import RiskEngine
from src.pricer.risk.var import VarEngine, VarResult

VDATE = date(2026, 5, 29)


# --- limits ---------------------------------------------------------------- #
def test_limit_monitor_flags_var_and_sensitivity_breaches():
    cfg = {"risk": {"limits": {"var": {"0.99": 1000.0}, "sensitivities": {"CS01": 500.0}}}}
    vr = VarResult("2026-05-29", 1, 250, 0, {}, var={"0.99": 2000.0})
    breaches = LimitMonitor(cfg).check(vr, {"CS01": -800.0, "IR_PARALLEL": 100.0})
    by_name = {b.name: b for b in breaches}
    assert "VaR[0.99]" in by_name and "CS01" in by_name      # both breached
    assert by_name["CS01"].value == 800.0                    # abs value
    assert all(b.value > b.limit for b in breaches)


def test_limit_monitor_clean_under_loose_limits():
    cfg = {"risk": {"limits": {"var": {"0.99": 1e9}, "sensitivities": {"CS01": 1e9}}}}
    vr = VarResult("d", 1, 250, 0, {}, var={"0.99": 100.0})
    assert LimitMonitor(cfg).check(vr, {"CS01": 10.0}) == []


def test_limit_monitor_no_config_no_breaches():
    assert LimitMonitor({}).check(None, {"CS01": 1e12}) == []


# --- backtest math --------------------------------------------------------- #
def test_basel_zone_traffic_light():
    # 250 days at 1% expected -> mean 2.5 exceptions.
    assert _basel_zone(2, 250, 0.01) == "green"
    assert _basel_zone(6, 250, 0.01) == "amber"
    assert _basel_zone(12, 250, 0.01) == "red"


def test_kupiec_lr_nonnegative_and_zero_at_expected():
    assert _kupiec_lr(250, 0, 0.01) > 0.0          # no exceptions still deviates from 1%
    # exception rate exactly == p -> LR ~ 0
    assert abs(_kupiec_lr(100, 1, 0.01)) < 1e-9


# --- dated exception timeline ---------------------------------------------- #
def test_backtest_timeline_records_dated_exceptions():
    # 20 days of small P&L with one large loss on a test day (index 18).
    vals = np.array([0.5, -0.5, 0.3, -0.2, 0.4, -0.1, 0.2, -0.3, 0.1, -0.4,
                     0.5, -0.5, 0.3, -0.2, 0.4, 0.1, -0.2, 0.3, -50.0, 0.2], dtype=float)
    dates = [f"2026-01-{i + 1:02d}" for i in range(20)]

    r = backtest_pnl(vals, level=0.99, window=10, test_days=5, dates=dates)
    assert r is not None
    assert len(r.timeline) == 5
    assert [p["date"] for p in r.timeline] == dates[15:20]

    exc = [p for p in r.timeline if p["exception"]]
    assert len(exc) == r.n_exceptions == 1
    big = next(p for p in r.timeline if p["date"] == "2026-01-19")   # index 18, the -50 loss
    assert big["pnl"] == -50.0
    assert big["exception"] is True
    assert big["var"] > 0.0                                          # loss threshold is positive $
    # the timeline round-trips through to_dict for persistence
    assert r.to_dict()["timeline"] == r.timeline


def test_backtest_timeline_uses_index_labels_without_dates():
    vals = np.linspace(-1.0, 1.0, 40)
    r = backtest_pnl(vals, level=0.95, window=20, test_days=10)  # no dates
    assert r is not None
    assert len(r.timeline) == 10
    # first test day is index 30 (n_total 40 - test 10); labels fall back to the index string
    assert r.timeline[0]["date"] == "30"


# --- backtest engine (real data, small window) ----------------------------- #
@pytest.mark.needs_market_data
def test_var_backtester_runs_on_book():
    cfg = load_pricer_config()
    root = get_project_root()
    risk = RiskEngine(PricingEngine(cfg, root), cfg)
    ve = VarEngine(risk, cfg, root)
    bt = VarBacktester(ve, {"risk": {"backtest": {"level": 0.99, "window": 30, "test_days": 20}}})
    ctx = ValuationContext(valuation_date=VDATE)
    r = bt.run(load_portfolio(), ctx)
    assert r is not None
    assert r.zone in ("green", "amber", "red")
    assert 0 <= r.n_exceptions <= r.n_days
    assert r.expected_exceptions == pytest.approx(r.n_days * 0.01)
