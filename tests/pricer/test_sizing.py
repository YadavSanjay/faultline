"""Risk-appetite position sizing (src/pricer/sizing).

Unit tests for the sizing numerics (risk-budget solve, concentration cap, expected
returns, VaR-scale solve) plus one real-data integration test asserting the trade-level
P&L matrix reconstructs the VaR engine's book VaR and the three profiles size coherently.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk.sensitivities import RiskEngine
from src.pricer.risk.var import VarEngine
from src.pricer.sizing.expected_returns import ExpectedReturnModel
from src.pricer.sizing.optimizer import (
    _apply_concentration_cap,
    _risk_budget,
    solve_profile,
)
from src.pricer.sizing.profiles import load_profiles
from src.pricer.sizing.sizing_runner import load_sizing_config
from src.pricer.sizing.trade_risk import TradeRisk, TradeRiskModel

VDATE = date(2026, 5, 29)


# --- risk-budget numerics -------------------------------------------------- #
def test_risk_budget_equal_budget_gives_equal_contributions():
    rng = np.random.default_rng(0)
    a = rng.normal(size=(6, 6))
    cov = a @ a.T + np.eye(6)                       # SPD
    x = _risk_budget(cov, np.ones(6))
    rc = x * (cov @ x)                              # component risk contributions
    assert np.allclose(rc, rc.mean(), rtol=1e-6)   # risk parity: all equal


def test_risk_budget_unequal_budget_matches_shares():
    rng = np.random.default_rng(1)
    a = rng.normal(size=(5, 5))
    cov = a @ a.T + np.eye(5)
    b = np.array([0.4, 0.3, 0.15, 0.1, 0.05])
    x = _risk_budget(cov, b)
    rc = x * (cov @ x)
    assert np.allclose(rc / rc.sum(), b / b.sum(), rtol=1e-5)


def test_concentration_cap_bounds_and_normalises():
    b = _apply_concentration_cap(np.array([0.6, 0.25, 0.1, 0.05]), cap=0.30)
    assert b.max() <= 0.30 + 1e-9
    assert b.sum() == pytest.approx(1.0)


def _dominant_model() -> TradeRiskModel:
    """4 directional trades where trade 0 carries ~3x the vol of the rest (all co-moving)."""
    rng = np.random.default_rng(7)
    z = rng.normal(size=(500, 4))
    cols = np.column_stack([
        3.0 * z[:, 0],
        0.5 * z[:, 0] + z[:, 1],
        0.5 * z[:, 0] + z[:, 2],
        0.5 * z[:, 0] + z[:, 3],
    ]) * 1e5
    trades = [TradeRisk(f"T{i}", "equity", f"t{i}", 5e7, 5e7, {}, is_hedge=False)
              for i in range(4)]
    return TradeRiskModel(trades, cols, [f"f{i}" for i in range(4)],
                          [str(i) for i in range(500)])


def test_risk_share_cap_bounds_realized_share():
    from src.pricer.sizing.optimizer import _apply_risk_share_cap
    model = _dominant_model()
    m0 = np.ones(4)
    share0 = model.risk_contributions(m0) / model.portfolio_vol(m0)
    assert share0[0] > 0.40                                 # trade 0 dominates before the cap
    m1, capped = _apply_risk_share_cap(model, m0, np.arange(4), set(), cap=0.35)
    assert capped
    share1 = model.risk_contributions(m1) / model.portfolio_vol(m1)
    assert share1.max() <= 0.35 + 1e-3                       # every eligible trade at/below the cap
    assert m1[0] < m0[0]                                     # the dominant name was trimmed


def test_risk_share_cap_never_trims_excluded():
    from src.pricer.sizing.optimizer import _apply_risk_share_cap
    model = _dominant_model()
    m0 = np.ones(4)
    # Excluding the only over-cap trade (0) leaves the book untouched (nothing else is over).
    m2, capped = _apply_risk_share_cap(model, m0, np.arange(4), {0}, cap=0.35)
    assert not capped
    assert np.allclose(m2, m0)


# --- synthetic trade-risk model -------------------------------------------- #
def _toy_model() -> TradeRiskModel:
    rng = np.random.default_rng(2)
    # Three trades: two correlated longs and one anti-correlated hedge.
    z = rng.normal(size=(400, 2))
    long1 = z[:, 0]
    long2 = 0.8 * z[:, 0] + 0.2 * z[:, 1]
    hedge = -0.9 * z[:, 0] + 0.1 * rng.normal(size=400)
    P = np.column_stack([long1, long2, hedge]) * 1e5
    trades = [
        TradeRisk("A", "equity", "long 1", 5e7, 5e7, {}, is_hedge=False),
        TradeRisk("B", "equity", "long 2", 5e7, 5e7, {}, is_hedge=False),
        TradeRisk("H", "equity", "hedge", -5e7, 5e7, {}, is_hedge=True),
    ]
    return TradeRiskModel(trades, P, ["f1", "f2"], [str(i) for i in range(400)])


def test_var_is_positively_homogeneous():
    m = _toy_model()
    w = np.array([1.0, 1.0, 1.0])
    assert m.var99(2 * w) == pytest.approx(2 * m.var99(w), rel=1e-9)


def test_risk_contributions_sum_to_vol():
    m = _toy_model()
    w = np.array([1.2, 0.8, 1.0])
    rc = m.risk_contributions(w)
    assert rc.sum() == pytest.approx(m.portfolio_vol(w), rel=1e-9)


def test_hedge_has_negative_risk_contribution():
    m = _toy_model()
    rc = m.risk_contributions(np.array([1.0, 1.0, 1.0]))
    assert rc[2] < 0.0                              # anti-correlated hedge reduces risk


# --- expected returns ------------------------------------------------------ #
def _er_model() -> ExpectedReturnModel:
    return ExpectedReturnModel({
        "expected_returns": {
            "funding_rate": 0.04, "equity_risk_premium": 0.05,
            "betas": {"NVDA": 2.0}, "growth_tilt": {"NVDA": 0.03},
            "foreign_rates": {"JPY": 0.005},
        }
    })


def test_expected_return_signs():
    er = _er_model()
    raw = [
        {"trade_id": "S", "trade_type": "stock", "notional": 5e7,
         "params": {"ticker": "NVDA"}},
        {"trade_id": "SH", "trade_type": "stock", "notional": 5e7,
         "params": {"ticker": "NVDA"}},
        {"trade_id": "CDS_S", "trade_type": "cds", "notional": 5e7,
         "params": {"protection": "sell", "running_coupon": 0.01}},
        {"trade_id": "CDS_B", "trade_type": "cds", "notional": 5e7,
         "params": {"protection": "buy", "running_coupon": 0.01}},
        {"trade_id": "BND", "trade_type": "corp_bond", "notional": 5e7,
         "params": {"coupon": 0.055}},
    ]
    pv = {"S": 5e7, "SH": -5e7, "CDS_S": 0.0, "CDS_B": 0.0, "BND": 5e7}
    out = er.compute(raw, pv, VDATE)
    assert out["S"].mu_annual > 0                   # long equity: positive drift
    assert out["SH"].mu_annual < 0                  # short equity: negative
    assert out["CDS_S"].mu_annual == pytest.approx(0.01 * 5e7)    # sell protection earns carry
    assert out["CDS_B"].mu_annual == pytest.approx(-0.01 * 5e7)   # buy protection pays
    assert out["BND"].mu_annual == pytest.approx((0.055 - 0.04) * 5e7)


# --- integration: real book ------------------------------------------------ #
@pytest.fixture(scope="module")
def real_model() -> tuple:
    cfg = load_pricer_config()
    root = get_project_root()
    risk = RiskEngine(PricingEngine(cfg, root), cfg)
    ctx = ValuationContext(valuation_date=VDATE)
    trades = load_portfolio()
    sizing_cfg = load_sizing_config(root)
    hedge_ids = set(sizing_cfg.get("hedge_trades", []))
    model = TradeRiskModel.build(trades, ctx, risk, cfg, root, hedge_ids)
    return model, cfg, root, risk, ctx, sizing_cfg


@pytest.mark.needs_market_data
def test_trade_pnl_matrix_reconstructs_book_var(real_model):
    _model, cfg, root, risk, ctx, _ = real_model
    # Reconcile like-for-like: build the trade matrix over the VaR engine's own window, and
    # compare to the *linear* book VaR (include_gamma=False) — the matrix is first-order by
    # construction, whereas the headline VaR additionally carries the equity gamma term.
    lookback = int(cfg["risk"]["var"]["lookback_days"])
    m = TradeRiskModel.build(load_portfolio(), ctx, risk, cfg, root, set(), lookback=lookback)
    ref = VarEngine(risk, cfg, root).historical_var(load_portfolio(), ctx, include_gamma=False)
    mine = m.var_es(np.ones(m.n_trades))
    # Summing per-trade P&L must equal the VaR engine's book P&L to the dollar.
    assert mine["var"]["0.99"] == pytest.approx(ref.var["0.99"], rel=1e-9)
    assert mine["es"]["0.975"] == pytest.approx(ref.es["0.975"], rel=1e-9)


@pytest.mark.needs_market_data
def test_profiles_size_coherently(real_model):
    model, _, _, _, _, sizing_cfg = real_model
    capital = float(sizing_cfg["capital_base"])
    er = ExpectedReturnModel(sizing_cfg).compute(
        [{"trade_id": t.trade_id, "trade_type": "", "notional": t.notional, "params": {}}
         for t in model.trades],
        {t.trade_id: t.pv for t in model.trades}, VDATE)
    profiles = load_profiles(sizing_cfg)
    results = {k: solve_profile(model, er, p, capital) for k, p in profiles.items()}

    for res in results.values():
        assert all(s.multiplier >= 0 for s in res.sizes)        # signs preserved
        assert np.isfinite(res.achieved_var99) and res.achieved_var99 > 0
        assert res.achieved_var99 <= res.var_budget * 1.05      # never above budget (+tol)

    # The core profile carries a realized single-name risk-share cap: no non-hedge trade may
    # exceed it (the concentration_cap only bounds the intended directional budget).
    core_cap = profiles["core"].risk_share_cap
    if core_cap < 1.0:
        worst = max(s.risk_share for s in results["core"].sizes if not s.is_hedge)
        assert worst <= core_cap + 5e-3

    # Risk appetite is monotonic: more VaR budget, more leverage, more risk taken.
    assert (results["balanced"].achieved_var99
            < results["aggressive"].achieved_var99
            < results["risky"].achieved_var99)
    assert (results["balanced"].gross_leverage
            < results["aggressive"].gross_leverage
            < results["risky"].gross_leverage)
