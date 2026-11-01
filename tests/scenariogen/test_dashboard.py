"""S5 dashboard tests: summary matrix, ranking, breaches, and the queryable store."""

from __future__ import annotations

from src.pricer.risk.scenarios import ScenarioPnl
from src.scenariogen.dashboard import ScenarioResultStore, StressDashboard, stress_rows
from src.scenariogen.plausibility import PlausibilityResult
from src.scenariogen.stress import ScenarioCapital, ScenarioStressResult

VDATE = "2026-05-29"


def _result(scenario, severity, pnl, cap_delta=0.0, status="pass"):
    pnls = [ScenarioPnl("T1", "equity", "USD", 100.0, 100.0 + pnl)]   # pnl = scenario - base
    cap = ScenarioCapital(0.975, 1_000_000.0, 1_000_000.0 + cap_delta, 250)
    return ScenarioStressResult(
        scenario=scenario, severity=severity, valuation_date=VDATE, results=pnls,
        capital=cap, plausibility=PlausibilityResult(scenario, status),
        materialized={"provenance": {"macro": {"used_macro": False, "world_view_id": ""}}})


def _sample():
    return [
        _result("taiwan", "mild", -1_000.0, cap_delta=5_000.0),
        _result("taiwan", "severe", -9_000.0, cap_delta=50_000.0),
        _result("ai_bust", "mild", -2_000.0, cap_delta=8_000.0),
        _result("ai_bust", "severe", -20_000.0, cap_delta=90_000.0),
    ]


def test_pnl_matrix_shape_and_order():
    dash = StressDashboard(_sample())
    m = dash.pnl_matrix()
    assert list(m.index) == ["ai_bust", "taiwan"] or set(m.index) == {"ai_bust", "taiwan"}
    assert list(m.columns) == ["mild", "severe"]            # severity-ordered
    assert m.loc["ai_bust", "severe"] == -20_000.0


def test_ranking_worst_first():
    r = StressDashboard(_sample()).ranking()
    assert r.iloc[0]["scenario"] == "ai_bust" and r.iloc[0]["severity"] == "severe"
    assert r.iloc[0]["total_pnl"] == -20_000.0


def test_breaches_by_loss_and_capital():
    dash = StressDashboard(_sample())
    loss_breaches = dash.breaches(loss_limit=8_000.0)
    assert set(zip(loss_breaches["scenario"], loss_breaches["severity"], strict=True)) == {
        ("taiwan", "severe"), ("ai_bust", "severe")}
    cap_breaches = dash.breaches(capital_limit=60_000.0)
    assert list(cap_breaches["scenario"]) == ["ai_bust"]    # only 90k delta breaches


def test_markdown_renders():
    md = StressDashboard(_sample()).to_markdown()
    assert "P&L by scenario" in md and "Capital" in md and "taiwan" in md


def test_empty_dashboard():
    dash = StressDashboard([])
    assert dash.pnl_matrix().empty and dash.to_markdown() == "_(no scenarios)_"


def test_result_store_roundtrip(tmp_path):
    store = ScenarioResultStore(tmp_path)
    store.write_run(stress_rows(_sample(), "run1"), "run1")
    latest = store.latest_run(VDATE)
    assert len(latest) == 4
    worst = store.worst_scenarios(VDATE, limit=1)
    assert worst.iloc[0]["scenario"] == "ai_bust" and worst.iloc[0]["total_pnl"] == -20_000.0
