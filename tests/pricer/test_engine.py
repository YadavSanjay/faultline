"""P1b PricingEngine tests: the library API, and that it matches the batch runner."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine, TradeOutcome
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.runner import run_to

pytestmark = pytest.mark.needs_market_data

VDATE = date(2026, 5, 29)


def _engine() -> PricingEngine:
    return PricingEngine(load_pricer_config(), get_project_root())


def test_price_returns_provenance_stamped_result():
    eng = _engine()
    ctx = ValuationContext(valuation_date=VDATE)
    trade = load_portfolio(trade_id="TRADE_22")[0]
    res = eng.price(trade, ctx)
    assert res.trade_id == "TRADE_22"
    assert res.pv != 0
    assert res.provenance["market_data_snapshot_id"] == eng.snapshot_id
    assert res.provenance["run_id"] and res.provenance["git_sha"]


def test_price_portfolio_outcomes_all_ok():
    eng = _engine()
    ctx = ValuationContext(valuation_date=VDATE)
    trades = load_portfolio(asset_class="rates")
    outcomes = eng.price_portfolio(trades, ctx)
    assert len(outcomes) == len(trades)
    assert {o.status for o in outcomes} == {"ok"}
    assert all(isinstance(o, TradeOutcome) and o.ok and o.result is not None for o in outcomes)


def test_engine_pv_matches_runner(tmp_path):
    """The thin runner driver and the engine must produce identical PVs."""
    # outputs go to tmp_path so the test never writes into the real data/pricer store
    rows = run_to(tmp_path, asset_class="rates", valuation_date=VDATE)
    eng = _engine()
    ctx = ValuationContext(valuation_date=VDATE)
    by_tid = {o.trade_id: o for o in eng.price_portfolio(load_portfolio(asset_class="rates"), ctx)}
    for r in rows:
        assert r["pv"] == by_tid[r["trade_id"]].result.pv
