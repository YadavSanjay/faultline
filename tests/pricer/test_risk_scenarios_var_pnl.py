"""P2 risk tests: scenarios (full-reval stress), VaR/ES, and P&L attribution."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk.pnl_explain import PnlExplainEngine
from src.pricer.risk.scenarios import Scenario, ScenarioEngine, load_scenarios
from src.pricer.risk.sensitivities import RiskEngine
from src.pricer.risk.var import VarEngine

VDATE = date(2026, 5, 29)


@pytest.fixture(scope="module")
def engine() -> PricingEngine:
    return PricingEngine(load_pricer_config(), get_project_root())


@pytest.fixture
def ctx() -> ValuationContext:
    return ValuationContext(valuation_date=VDATE)


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #
def test_scenario_transforms_count():
    s = Scenario("x", ir_bp=100, equity_pct=-25, fx_pct=8)
    assert len(s.transforms()) == 3   # only the non-zero shocks


def test_config_scenarios_loaded():
    scns = load_scenarios(load_pricer_config())
    assert {"taiwan_geopolitical", "ai_capex_bust", "rates_selloff_100bp"} <= set(scns)


@pytest.mark.needs_market_data
def test_rates_selloff_moves_every_rates_trade(engine, ctx):
    scn = load_scenarios(load_pricer_config())["rates_selloff_100bp"]
    res = ScenarioEngine(engine).run(scn, load_portfolio(asset_class="rates"), ctx)
    assert len(res) == 4
    assert all(r.scenario_pv != r.base_pv for r in res)


@pytest.mark.needs_market_data
def test_ai_capex_boom_helps_long_equity(engine, ctx):
    scn = load_scenarios(load_pricer_config())["ai_capex_boom"]   # +20% equities
    res = ScenarioEngine(engine).run(scn, load_portfolio(trade_id="TRADE_1"), ctx)
    assert res[0].pnl == pytest.approx(res[0].base_pv * 0.20, rel=1e-6)   # linear long


# --------------------------------------------------------------------------- #
# VaR / Expected Shortfall
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_var_es_monotonic_and_populated(engine, ctx):
    cfg = load_pricer_config()
    ve = VarEngine(RiskEngine(engine, cfg), cfg, get_project_root())
    r = ve.historical_var(load_portfolio(), ctx)
    assert r.n_scenarios > 50
    assert r.var["0.99"] >= r.var["0.95"] > 0          # higher confidence -> higher VaR
    assert r.es["0.975"] >= r.var["0.975"]             # ES never below VaR
    # multi-factor (GAP 7): equity delta keyed per underlier, FX per currency;
    # equity vega keyed per underlier (GAP 4 — AAPL uses its own VXAPL index)
    assert any(k.startswith("EQUITY_DELTA:") for k in r.book_sensitivities)
    assert any(k.startswith("FX_DELTA:") for k in r.book_sensitivities)
    assert "VEGA_EQUITY:AAPL" in r.book_sensitivities
    assert "VXAPL" in cfg["risk"]["var"]["factors"]["EQVOL_AAPL"]["file"]
    # IR is key-rate across the DGS grid (GAP 3), not a single parallel point
    assert sum(k.startswith("IR_KR:") for k in r.book_sensitivities) >= 6
    # FX foreign-rate rho is measured per currency (GAP 5); JPY is fed from TONAR history
    assert any(k.startswith("FX_RATE:") for k in r.book_sensitivities)
    assert "FXRATE_JPY" in cfg["risk"]["var"]["factors"]
    # equity vol surface: term + skew factors beyond the level (GAP 4)
    assert "VEGA_EQ_TERM" in r.book_sensitivities and "VEGA_EQ_SKEW" in r.book_sensitivities
    assert {"EQVOL_TERM", "EQVOL_SKEW"} <= set(cfg["risk"]["var"]["factors"])


# --------------------------------------------------------------------------- #
# P0 de-alias (market-intelligence FINDING 10): collinear EQVOL_* factors that all
# read VIX are collapsed into one multi-key EQVOL_VIX; QQQ moves to its own VXN index.
# --------------------------------------------------------------------------- #
def test_maps_to_accepts_string_or_list():
    from src.pricer.risk.var import _maps_to

    assert _maps_to({"maps_to": "VEGA_EQUITY:NVDA"}) == ["VEGA_EQUITY:NVDA"]
    assert _maps_to({"maps_to": ["A", "B", "C"]}) == ["A", "B", "C"]
    assert _maps_to({"maps_to": ""}) == []
    assert _maps_to({}) == []
    assert _maps_to({"maps_to": ["A", "", "B"]}) == ["A", "B"]   # empties dropped


def test_p0_dealias_no_two_eqvol_factors_share_a_series():
    """Every EQVOL_* level factor must resolve to a DISTINCT (file, column) — the point of P0.

    Before P0 six EQVOL_* factors all read VIX_History.csv (perfectly collinear columns). Scoped
    to EQVOL level factors: the IR key-rate grid intentionally proxies 9M -> DGS1 (no DGS9MO), and
    EQVOL_TERM/_SKEW read a spread/scaled series, so those legitimately overlap on a base column.
    """
    factors = load_pricer_config()["risk"]["var"]["factors"]
    seen: dict[tuple[str, str], str] = {}
    for name, fcfg in factors.items():
        if not name.startswith("EQVOL_") or name in ("EQVOL_TERM", "EQVOL_SKEW"):
            continue
        src = (str(fcfg.get("file", "")), str(fcfg.get("column", "")))
        assert src not in seen, f"{name} and {seen[src]} both read {src} — collinear (P0 regressed)"
        seen[src] = name
    # QQQ now tracks the Nasdaq-100 vol index, not VIX
    assert "VXN" in factors["EQVOL_QQQ"]["file"]
    # the shared VIX proxy is one factor carrying several names' vega
    assert isinstance(factors["EQVOL_VIX"]["maps_to"], list)
    assert "VEGA_EQUITY:SPX" in factors["EQVOL_VIX"]["maps_to"]


@pytest.mark.needs_market_data
def test_p0_multikey_factor_pnl_equals_sum_of_single_key(engine, ctx):
    """A multi-key EQVOL_VIX must give the exact P&L of N single-key factors on the same series.

    That equivalence is what makes the collapse P&L-neutral (only the honest factor count and the
    non-singular parametric cross-check change), so it is the invariant worth pinning.
    """
    cfg = load_pricer_config()
    ve = VarEngine(RiskEngine(engine, cfg), cfg, get_project_root())

    keys = ["VEGA_EQUITY:NVDA", "VEGA_EQUITY:MSFT", "VEGA_EQUITY:SPX"]
    book = {"VEGA_EQUITY:NVDA": 100.0, "VEGA_EQUITY:MSFT": 250.0, "VEGA_EQUITY:SPX": -40.0}
    vix = {"file": "options/vol_indices/VIX_History.csv", "column": "close",
           "proxy_bp_per_move_pt": 1.0}

    # One combined factor vs three separate ones reading the same VIX series.
    ve.cfg = {"factors": {"EQVOL_VIX": {**vix, "maps_to": keys}}}
    combined, _ = ve._factor_pnl_columns(book, include_gamma=False)

    ve.cfg = {"factors": {k.replace("VEGA_EQUITY:", "EQVOL_"): {**vix, "maps_to": k} for k in keys}}
    separate, _ = ve._factor_pnl_columns(book, include_gamma=False)

    import pandas as pd
    lhs = combined["EQVOL_VIX"]
    rhs = pd.concat(separate, axis=1).sum(axis=1)
    pd.testing.assert_series_equal(lhs, rhs, check_names=False)


# --------------------------------------------------------------------------- #
# P&L attribution
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_rates_vol_term_vega_differentiates_by_expiry(engine, ctx):
    """GAP 6: term-structure rates-vega is measured per swaption/cap expiry."""
    risk = RiskEngine(engine, load_pricer_config())
    euro = risk.sensitivities(load_portfolio(trade_id="TRADE_23")[0], ctx).by_factor()  # 5Y expiry
    berm = risk.sensitivities(load_portfolio(trade_id="TRADE_24")[0], ctx).by_factor()  # 2Y expiry
    assert "VEGA_RATES_TERM" in euro and "VEGA_RATES_TERM" in berm
    # 5Y is the ref tenor (g=1) so term-vega == level vega; 2Y is anchored lower (g<1)
    assert euro["VEGA_RATES_TERM"] == pytest.approx(euro["VEGA_RATES"], rel=1e-6)
    assert 0.0 < berm["VEGA_RATES_TERM"] < berm["VEGA_RATES"]


@pytest.mark.needs_market_data
def test_pnl_explain_linear_swap_fully_covered(engine, ctx):
    cfg = load_pricer_config()
    risk = RiskEngine(engine, cfg)
    pe = PnlExplainEngine(risk, ScenarioEngine(engine))
    scn = load_scenarios(cfg)["rates_selloff_100bp"]
    rep = pe.explain_scenario(scn, load_portfolio(trade_id="TRADE_22"), ctx)
    # a vanilla swap is near-linear in rates: DV01 x 100bp explains it up to convexity
    # (the linear term slightly over-explains the 100bp move).
    assert rep.coverage == pytest.approx(1.0, abs=0.10)
    assert abs(rep.residual_total) < abs(rep.actual_total) * 0.10


@pytest.mark.needs_market_data
def test_pnl_explain_book_has_residual(engine, ctx):
    cfg = load_pricer_config()
    pe = PnlExplainEngine(RiskEngine(engine, cfg), ScenarioEngine(engine))
    scn = load_scenarios(cfg)["taiwan_geopolitical"]
    rep = pe.explain_scenario(scn, load_portfolio(), ctx)
    assert rep.actual_total != 0.0
    assert set(rep.explained_by_factor()) & {"ir", "credit", "equity_delta", "fx"}
    assert 0.0 < rep.coverage < 1.2     # first-order explains most, not all
