"""The sizing optimizer's final leverage guardrail (sizing-leverage-cap).

The locked spec tests cover the case the fix is for: the leverage cap and the risk-share cap both
bind. These guard the behaviour around it that must not change: with the leverage cap slack the book
still lands exactly on its VaR budget (with or without the risk-share trim), and the final guardrail
holds the cap whichever step pushed gross over it — here the market-neutral pair pin.
"""

from __future__ import annotations

import numpy as np
import pytest
from src.pricer.sizing.expected_returns import ExpectedReturn
from src.pricer.sizing.optimizer import solve_profile
from src.pricer.sizing.profiles import RiskProfile
from src.pricer.sizing.trade_risk import TradeRisk, TradeRiskModel

CAPITAL = 1e9
N_DAYS = 250


def _model(n: int, seed: int = 0, notional: float = 50e6) -> TradeRiskModel:
    pnl = np.random.default_rng(seed).standard_normal((N_DAYS, n)) * 1e6
    trades = [TradeRisk(f"T{j}", "Equity", "", 0.0, notional, {},
                        standalone_vol=float(pnl[:, j].std(ddof=1))) for j in range(n)]
    return TradeRiskModel(trades, pnl, ["F"], [str(i) for i in range(N_DAYS)])


def _mu(values: list[float]) -> dict[str, ExpectedReturn]:
    return {f"T{j}": ExpectedReturn(f"T{j}", v, "synthetic") for j, v in enumerate(values)}


def _profile(lev_cap: float, risk_share_cap: float = 1.0) -> RiskProfile:
    return RiskProfile(key="t", label="t", var_budget_pct=0.05, gross_leverage_cap=lev_cap,
                       concentration_cap=1.0, risk_share_cap=risk_share_cap, notional_cap_pct=10.0,
                       hedge_scale=1.0, method="return_tilt", tilt_power=2.0, sharpe_floor=0.01)


def _gross(res) -> float:
    return sum(abs(s.target_notional) for s in res.sizes) / CAPITAL


def test_slack_leverage_lands_exactly_on_var_budget():
    res = solve_profile(_model(4), _mu([5e6, 4e6, 3e6, 2e6]), _profile(lev_cap=100.0), CAPITAL)
    assert res.binding_constraint == "var_budget"
    # VaR is positively homogeneous, so the scale-to-budget solve lands on it to float precision.
    assert res.achieved_var99 == pytest.approx(0.05 * CAPITAL, rel=1e-9)
    assert _gross(res) < 100.0


def test_slack_leverage_keeps_risk_share_cap_and_var_budget():
    res = solve_profile(_model(4), _mu([50e6, 1e6, 1e6, 1e6]), _profile(lev_cap=100.0, risk_share_cap=0.30),
                        CAPITAL)
    assert res.binding_constraint == "risk_share_cap"
    assert max(s.risk_share for s in res.sizes) <= 0.30 + 1e-4   # the trim's documented tolerance
    assert res.achieved_var99 == pytest.approx(0.05 * CAPITAL, rel=1e-9)
    assert _gross(res) < 100.0


def test_guardrail_holds_cap_when_pair_pin_pushes_gross_over_it():
    # T0 dominates the tilt; T1 is floored to a sliver. Pinning T1 (short leg) to 2x T0's notional
    # adds gross after the step-5 leverage clip, which used to leave the book above the cap.
    model = _model(3)
    pairs = [{"long": "T0", "short": "T1", "short_ratio": 2.0}]
    res = solve_profile(model, _mu([50e6, 0.0, 1e6]), _profile(lev_cap=0.5), CAPITAL, pairs=pairs)
    assert _gross(res) <= 0.5 * (1 + 1e-9)
    assert res.binding_constraint == "leverage_cap"
    by_id = {s.trade_id: s.target_notional for s in res.sizes}
    # The uniform scale-down keeps the pinned leg ratio.
    assert abs(by_id["T1"]) / abs(by_id["T0"]) == pytest.approx(2.0, rel=1e-12)
