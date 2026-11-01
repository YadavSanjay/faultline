"""S5 reverse-stress tests: solve the severity that drives loss to a target."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk.factor_history import FactorHistory
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.reverse import ReverseStressSolver
from src.scenariogen.scenario_gen import ScenarioGenerator

VDATE = date(2026, 5, 29)


def _setup():
    cfg = load_pricer_config()
    root = get_project_root()
    engine = PricingEngine(cfg, root)
    catalog = ScenarioCatalog.from_config(FactorHistory(root, cfg), lookback=60)
    solver = ReverseStressSolver(engine, ScenarioGenerator(catalog.covariance),
                                 max_scale=8.0, tol_rel=0.02, max_iter=25)
    defn = catalog.definition("keyman_nvda", "adverse")        # loss-making for the equity book
    trades = load_portfolio(asset_class="equity")
    ctx = ValuationContext(valuation_date=VDATE)
    return solver, defn, trades, ctx


@pytest.mark.needs_market_data
def test_loss_increases_with_severity():
    solver, defn, trades, ctx = _setup()
    assert solver.loss_at(defn, 2.0, trades, ctx) > solver.loss_at(defn, 0.5, trades, ctx) > 0


@pytest.mark.needs_market_data
def test_reachable_target_is_solved():
    solver, defn, trades, ctx = _setup()
    ref = solver.loss_at(defn, solver.max_scale, trades, ctx)   # max attainable loss
    target = 0.4 * ref
    r = solver.solve(defn, trades, ctx, target)
    assert r.reachable
    assert 0.0 < r.severity_scale < solver.max_scale
    assert abs(r.achieved_loss - target) <= 0.05 * target      # within solver tolerance
    assert r.materialized["name"] == "keyman_nvda"


@pytest.mark.needs_market_data
def test_unreachable_target_flagged():
    solver, defn, trades, ctx = _setup()
    ref = solver.loss_at(defn, solver.max_scale, trades, ctx)
    r = solver.solve(defn, trades, ctx, target_loss=ref * 5.0)
    assert not r.reachable
    assert r.severity_scale == solver.max_scale


def test_reverse_stress_report_persists(tmp_path: Path):
    """The report bundles results + provenance and writes an atomic reverse_<run_id>.json."""
    from src.scenariogen.report import ReverseStressReport, write_reverse_stress_report

    rep = ReverseStressReport(
        valuation_date="2026-05-29", run_id="RID", snapshot_id="SNAP",
        target_loss=50_000_000.0,
        results=[{
            "scenario": "keyman_nvda", "reachable": True, "severity_scale": 0.3,
            "achieved_loss": 50_300_000.0, "target_loss": 50_000_000.0, "iterations": 12,
            "materialized": {"name": "keyman_nvda", "drivers": ["eq:TSM"], "vector": {"eq:TSM": -0.2}},
        }],
    )
    out = write_reverse_stress_report(rep, tmp_path)
    assert out.name == "reverse_RID.json"
    assert out.parent == tmp_path / "data" / "pricer" / "scenario" / "2026-05-29"

    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["target_loss"] == 50_000_000.0
    assert d["snapshot_id"] == "SNAP"
    assert d["results"][0]["scenario"] == "keyman_nvda"
    assert d["results"][0]["materialized"]["vector"]["eq:TSM"] == -0.2
    assert "generated_at" in d
