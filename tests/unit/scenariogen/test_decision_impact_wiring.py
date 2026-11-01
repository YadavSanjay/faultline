"""scenario-decision-impact wiring: the optimizer's stress-loss step and the runner's scenario sets.

A toy trade-risk model and synthetic reports; no ``data/``, services or QuantLib.
"""

from __future__ import annotations

import numpy as np
import pytest
from src.pricer.sizing.expected_returns import ExpectedReturn
from src.pricer.sizing.optimizer import solve_profile
from src.pricer.sizing.profiles import load_profiles
from src.pricer.sizing.sizing_runner import load_sizing_config
from src.pricer.sizing.stress_constraint import StressInput
from src.pricer.sizing.trade_risk import TradeRisk, TradeRiskModel
from src.scenariogen.decision_impact_runner import (
    compare,
    over_budget,
    sets_from_ablation_report,
    sets_from_stress_report,
    stress_budget,
)

CAPITAL = 1e9


def _model() -> TradeRiskModel:
    rng = np.random.default_rng(2)
    z = rng.normal(size=(400, 2))
    pnl = np.column_stack([z[:, 0], 0.8 * z[:, 0] + 0.2 * z[:, 1], -0.9 * z[:, 0] + 0.1 * rng.normal(size=400)])
    trades = [TradeRisk("A", "equity", "long 1", 5e7, 5e7, {}, is_hedge=False),
              TradeRisk("B", "equity", "long 2", 5e7, 5e7, {}, is_hedge=False),
              TradeRisk("H", "equity", "hedge", -5e7, 5e7, {}, is_hedge=True)]
    return TradeRiskModel(trades, pnl * 1e5, ["f1", "f2"], [str(i) for i in range(400)])


def _mu(model: TradeRiskModel) -> dict[str, ExpectedReturn]:
    return {t.trade_id: ExpectedReturn(t.trade_id, 1e6, "test") for t in model.trades}


def _profile():
    return load_profiles(load_sizing_config())["core"]


def test_budget_is_var_multiple_times_the_daily_var_budget():
    prof = _profile()
    assert stress_budget(prof, CAPITAL, 6.0) == pytest.approx(6.0 * prof.var_budget_pct * CAPITAL)
    cfg = load_sizing_config()["stress_constraint"]
    assert cfg == {"tier": "adverse", "var_multiple": 6.0}


def test_stress_step_binds_scales_the_book_and_reports():
    model, prof = _model(), _profile()
    free = solve_profile(model, _mu(model), prof, CAPITAL)
    m_free = np.array([s.multiplier for s in sorted(free.sizes, key=lambda s: model.trade_ids.index(s.trade_id))])
    pnl = np.array([[-4e7, -3e7, 1e7], [-1e7, 0.0, 0.0]])          # base-book P&L per scenario x trade
    loss_free = float(-(pnl[0] @ m_free))
    budget = 0.5 * loss_free
    res = solve_profile(model, _mu(model), prof, CAPITAL, stress=StressInput(pnl, ("crash", "mild"), budget))
    assert res.binding_constraint == "stress_loss"
    assert res.worst_stress_scenario == "crash"
    assert res.worst_stress_loss == pytest.approx(budget, rel=1e-9)            # lands on the budget
    ratio = {s.trade_id: s.multiplier for s in res.sizes}
    for s in free.sizes:
        assert ratio[s.trade_id] == pytest.approx(0.5 * s.multiplier, rel=1e-9)  # homogeneous rescale
    assert res.as_dict()["stress"] == {"budget": budget, "worst_scenario": "crash",
                                       "worst_loss": pytest.approx(budget, rel=1e-9)}


def test_without_stress_input_the_result_is_unchanged():
    model, prof = _model(), _profile()
    a = solve_profile(model, _mu(model), prof, CAPITAL).as_dict()
    b = solve_profile(model, _mu(model), prof, CAPITAL).as_dict()
    assert a == b and "stress" not in a
    loose = solve_profile(model, _mu(model), prof, CAPITAL,
                          stress=StressInput(np.array([[-1.0, -1.0, 0.0]]), ("tiny",), 1e12))
    assert loose.binding_constraint == a["binding_constraint"]                 # a loose budget doesn't bind
    assert [s.multiplier for s in loose.sizes] == [s["multiplier"] for s in a["sizes"]]


def _res(name: str, tier: str, vec: dict | None, source: str = "static") -> dict:
    return {"scenario": name, "severity": tier, "source": source, "materialized": {"vector": vec} if vec else {}}


def test_stress_report_set_takes_the_tier_and_skips_llm_and_vectorless():
    rep = {"run_id": "r", "scenarios": [_res("a", "adverse", {"eq:XLK": -0.2}), _res("a", "mild", {"eq:XLK": -0.1}),
                                        _res("b", "adverse", {"eq:NVDA": -0.3}),
                                        _res("c", "adverse", {"eq:TSM": -0.4}, source="llm"),
                                        _res("d", "adverse", None)]}
    (s,) = sets_from_stress_report(rep, "adverse")
    assert s.label == "stress" and s.vectors == {"a": {"eq:XLK": -0.2}, "b": {"eq:NVDA": -0.3}}
    with pytest.raises(ValueError, match="no severe result"):
        sets_from_stress_report(rep, "severe")


def test_ablation_report_sets_are_labelled_by_subset():
    order = ["macrorag", "news", "state"]

    def sub(layers: list[str], x: float) -> dict:
        return {"layers": layers, "vector": {"eq:XLK": x}}

    rep = {"run_id": "ab", "order": order, "scenarios": [
        {"scenario": "s1", "subsets": [sub(order, -0.3), sub([], -0.1), sub(["news", "state"], -0.2),
                                       sub(["macrorag", "state"], -0.25), sub(["state"], -0.15)]},
        {"scenario": "s2", "subsets": [sub(order, -0.5), sub([], -0.4)]}]}
    sets = {s.label: s for s in sets_from_ablation_report(rep)}
    assert set(sets) == {"all", "none", "without:macrorag", "without:news", "state"}
    assert sets["all"].vectors == {"s1": {"eq:XLK": -0.3}, "s2": {"eq:XLK": -0.5}}
    assert sets["without:news"].layers == ("macrorag", "state")
    with pytest.raises(ValueError, match="re-run the ablation"):
        sets_from_ablation_report({"run_id": "old", "order": order,
                                   "scenarios": [{"scenario": "s", "subsets": [{"layers": order}]}]})


def test_compare_and_over_budget():
    model, prof = _model(), _profile()
    free = solve_profile(model, _mu(model), prof, CAPITAL)
    pnl = np.array([[-4e7, -3e7, 1e7]])
    m_free = np.array([{s.trade_id: s.multiplier for s in free.sizes}[t] for t in model.trade_ids])
    loss = float(-(pnl[0] @ m_free))
    res = solve_profile(model, _mu(model), prof, CAPITAL, stress=StressInput(pnl, ("crash",), 0.25 * loss))
    diff = compare(free, res)
    assert diff["binding"]["to"] == "stress_loss" and diff["n_trades_changed"] == 3
    assert all(v["ratio"] == pytest.approx(0.25, rel=1e-9) for v in diff["multipliers"].values())
    assert over_budget(m_free, pnl, ["crash"], 0.25 * loss) == ["crash"]
    m_res = m_free * 0.25
    assert over_budget(m_res, pnl, ["crash"], 0.25 * loss * (1 + 1e-9)) == []
