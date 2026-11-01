"""S2 stress-engine tests: full-reval P&L + ES-under-shock capital."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.risk.historical_var import FullRevalVarEngine
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.stress import ScenarioStressEngine

pytestmark = pytest.mark.needs_market_data

VDATE = date(2026, 5, 29)


def _setup() -> tuple[ScenarioStressEngine, ScenarioCatalog, list]:
    cfg = load_pricer_config()
    root = get_project_root()
    engine = PricingEngine(cfg, root)
    var_engine = FullRevalVarEngine(engine, cfg, root)
    catalog = ScenarioCatalog.from_config(FactorHistory(root, cfg), lookback=60)
    trades = load_portfolio(trade_id="TRADE_1") + load_portfolio(trade_id="TRADE_22")
    return ScenarioStressEngine(engine, var_engine), catalog, trades


def test_pnl_only_run():
    stress, catalog, trades = _setup()
    ctx = ValuationContext(valuation_date=VDATE)
    ms = catalog.materialize("taiwan_geopolitical", "adverse")
    res = stress.run(ms, trades, ctx, with_capital=False)

    assert res.scenario == "taiwan_geopolitical" and res.severity == "adverse"
    assert res.capital is None
    assert len(res.results) > 0
    assert res.total_pnl != 0.0
    assert set(res.by_asset_class()) <= {"equity", "rates"}


def test_capital_es_under_shock():
    stress, catalog, trades = _setup()
    ctx = ValuationContext(valuation_date=VDATE)
    ms = catalog.materialize("keyman_nvda", "severe")
    res = stress.run(ms, trades, ctx, with_capital=True, var_lookback=40)

    cap = res.capital
    assert cap is not None
    assert cap.level == 0.975
    assert cap.lookback_days == 40
    assert cap.es_base > 0.0 and cap.es_shocked > 0.0
    # capital_delta is the change in required capital (ES) from today's to the shocked
    # state; its sign depends on book convexity/direction, so we assert the mechanism.
    assert cap.capital_delta == cap.es_shocked - cap.es_base


def test_result_to_dict_shape():
    stress, catalog, trades = _setup()
    ctx = ValuationContext(valuation_date=VDATE)
    res = stress.run(catalog.materialize("rate_capital", "mild"), trades, ctx,
                     with_capital=False)
    d = res.to_dict()
    assert d["scenario"] == "rate_capital"
    assert "pnl_by_asset_class" in d and "materialized" in d
    assert d["materialized"]["provenance"]["covariance"]["cov_id"] == catalog.covariance.cov_id
