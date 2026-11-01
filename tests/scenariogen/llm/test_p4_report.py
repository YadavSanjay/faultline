"""P4 test: LLM scenarios are written into a StressReport (source='llm' + provenance).

Uses the REAL pricing service (rates only, no capital) to reprice a calibrated LLM scenario and
verify the runner's report writer produces the standard StressReport the BFF/UI read, with each
scenario tagged `source: "llm"` and an `llm` provenance block.
"""

from __future__ import annotations

import json

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.portfolio.portfolio_loader import load_portfolio_config
from src.scenariogen.llm.pricing import ScenarioPricingService
from src.scenariogen.llm.runner import _write_stress_report
from src.scenariogen.llm.schema import (
    CalibratedScenario,
    Citation,
    CostSummary,
    DriverLeg,
    LLMScenarioArtifact,
)

pytestmark = pytest.mark.needs_market_data


def test_write_llm_stress_report(tmp_path) -> None:
    root = get_project_root()
    vdate = load_portfolio_config().default_valuation_date
    pricer = ScenarioPricingService(
        root=root, config=load_pricer_config(), valuation_date=vdate,
        asset_class="rates", with_capital=False,
    )
    cal = CalibratedScenario(
        name="rates_up", trigger="rate hike", world_view_id="macro-2026-06-24",
        legs=[DriverLeg(factor="ir:DGS10", move=0.005, origin="macro",
                        citations=[Citation(chunk_id="c1", source="macro")])],
    )
    artifact = LLMScenarioArtifact(
        run_id="llm-test-1", mode="hypothetical", models={"propose": "gpt-5"},
        calibrated=[cal], cost=CostSummary(total_cost_usd=0.05, n_llm_calls=7, n_tool_calls=8),
    )

    # Write into tmp_path (the report writer's root); the pricer keeps the real project root.
    out = _write_stress_report(tmp_path, pricer, artifact)
    assert out is not None and out.exists()
    # written to the standard scenario dir the BFF reads
    assert out.parent == tmp_path / "data" / "pricer" / "scenario" / vdate.isoformat()

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["run_id"] == "llm-test-1"
    scenario = data["scenarios"][0]
    assert scenario["source"] == "llm"
    assert scenario["scenario"] == "rates_up"
    assert isinstance(scenario["total_pnl"], float)
    llm = scenario["llm"]
    assert llm["mode"] == "hypothetical"
    assert llm["world_view_id"] == "macro-2026-06-24"
    assert llm["narrative"] == "rates_up.md"
    assert llm["total_cost_usd"] == 0.05
    assert llm["legs"][0] == {"factor": "ir:DGS10", "origin": "macro", "n_citations": 1}


def test_write_llm_stress_report_no_scenarios(tmp_path) -> None:
    root = get_project_root()
    vdate = load_portfolio_config().default_valuation_date
    pricer = ScenarioPricingService(
        root=root, config=load_pricer_config(), valuation_date=vdate,
        asset_class="rates", with_capital=False,
    )
    artifact = LLMScenarioArtifact(run_id="empty", mode="comprehensive")
    assert _write_stress_report(tmp_path, pricer, artifact) is None
