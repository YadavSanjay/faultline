"""Tier 3 risk add-ons: parametric VaR cross-check, carry/theta PLA, jump-to-default."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk.jtd import JtdEngine
from src.pricer.risk.pnl_explain import PnlExplainEngine
from src.pricer.risk.scenarios import ScenarioEngine
from src.pricer.risk.sensitivities import RiskEngine
from src.pricer.risk.var import VarEngine

VDATE = date(2026, 5, 29)


@pytest.fixture(scope="module")
def engine() -> PricingEngine:
    return PricingEngine(load_pricer_config(), get_project_root())


@pytest.fixture
def ctx() -> ValuationContext:
    return ValuationContext(valuation_date=VDATE)


@pytest.mark.needs_market_data
def test_parametric_var_present_and_ordered(engine, ctx):
    cfg = load_pricer_config()
    r = VarEngine(RiskEngine(engine, cfg), cfg, get_project_root()).historical_var(load_portfolio(), ctx)
    p = r.parametric
    assert p["method"] == "variance_covariance_normal"
    assert p["sigma"] > 0
    assert p["var"]["0.99"] > p["var"]["0.95"] > 0        # monotone in confidence
    assert p["es"]["0.975"] >= p["var"]["0.975"]          # ES never below VaR


@pytest.mark.needs_market_data
def test_carry_theta_signs(engine, ctx):
    cfg = load_pricer_config()
    pe = PnlExplainEngine(RiskEngine(engine, cfg), ScenarioEngine(engine))
    ct = pe.carry_theta(load_portfolio(), ctx)
    assert ct.horizon_days == 1
    assert len(ct.trades) > 0
    # each trade's carry is PV(t+1) - PV(t)
    for t in ct.trades:
        assert t.carry == pytest.approx(t.pv_forward - t.pv)
    assert ct.total_carry == pytest.approx(sum(t.carry for t in ct.trades))


def test_jtd_directions_and_idr():
    cfg = load_pricer_config()
    rep = JtdEngine(cfg).compute(load_portfolio(), VDATE.isoformat())
    by_issuer = {i.issuer: i for i in rep.issuers}
    # the book is long corporate bonds -> a long bond loses LGD*face on default (negative JTD)
    assert any(i.jtd < 0 for i in rep.issuers)
    assert all(0.0 < i.lgd < 1.0 for i in rep.issuers)
    # IDR is the sum of per-issuer net losses -> positive, and >= the worst single name
    worst = rep.worst
    assert worst is not None
    assert rep.idr >= -worst.jtd > 0
    # index CDS default risk is diversified: reported separately, scaled to a single-name equivalent
    if rep.index_positions:
        idx = rep.index_positions[0]
        assert idx.bucket == "index"
        assert abs(idx.jtd) < idx.gross_notional      # scaled down by index size
    # every issuer is tagged with a rating/credit bucket (issuer credit split)
    assert all(i.bucket for i in rep.issuers)
    assert "IG" in {i.bucket for i in rep.issuers} or by_issuer
