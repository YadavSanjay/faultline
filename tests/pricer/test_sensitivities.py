"""P2 sensitivities tests: bump-and-revalue Greeks, with analytic sanity checks."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk.sensitivities import RiskEngine

pytestmark = pytest.mark.needs_market_data

VDATE = date(2026, 5, 29)


@pytest.fixture(scope="module")
def risk() -> RiskEngine:
    return RiskEngine(PricingEngine(load_pricer_config(), get_project_root()))


@pytest.fixture
def ctx() -> ValuationContext:
    return ValuationContext(valuation_date=VDATE)


def _sens(risk, ctx, tid, **kw):
    return risk.sensitivities(load_portfolio(trade_id=tid)[0], ctx, **kw)


def test_cash_equity_delta_is_one_percent_of_pv(risk, ctx):
    # A linear long position: 1% bump delta == pv/100 exactly, gamma == 0.
    r = _sens(risk, ctx, "TRADE_1")
    bf = r.by_factor()
    assert bf["EQUITY_DELTA"] == pytest.approx(r.pv / 100.0, rel=1e-9)
    assert bf["EQUITY_GAMMA"] == pytest.approx(0.0, abs=1e-3)


def test_swap_keyrates_sum_to_parallel(risk, ctx):
    r = _sens(risk, ctx, "TRADE_22", key_rates=True)
    bf = r.by_factor()
    kr_sum = sum(v for k, v in bf.items() if k.startswith("IR_KR:"))
    assert bf["IR_PARALLEL"] != 0.0
    assert kr_sum == pytest.approx(bf["IR_PARALLEL"], rel=2e-2)   # buckets ~ parallel


def test_corporate_bond_signs(risk, ctx):
    bf = _sens(risk, ctx, "TRADE_15", key_rates=False).by_factor()
    assert bf["IR_PARALLEL"] < 0     # long bond loses value when rates rise
    assert bf["CS01"] < 0            # ... and when its credit spread widens


def test_cds_cs01_positive_for_protection_buyer(risk, ctx):
    bf = _sens(risk, ctx, "TRADE_28", key_rates=False).by_factor()
    assert bf["CS01"] > 0            # protection buyer gains as spreads widen


def test_option_has_vega_delta_gamma(risk, ctx):
    bf = _sens(risk, ctx, "TRADE_7", key_rates=False).by_factor()
    assert bf["VEGA_EQUITY"] > 0     # long option is long vol
    assert "EQUITY_DELTA" in bf and "EQUITY_GAMMA" in bf
    assert "IR_PARALLEL" in bf       # rho from the SOFR discount curve


def test_to_dict_roundtrip(risk, ctx):
    d = _sens(risk, ctx, "TRADE_1").to_dict()
    assert d["trade_id"] == "TRADE_1" and d["sensitivities"]
    assert {"factor", "value", "bump_unit", "method"} <= set(d["sensitivities"][0])
