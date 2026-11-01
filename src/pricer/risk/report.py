"""Risk report aggregation + persistence (P2).

Bundles the run's sensitivities, stress results, VaR/ES, and (optional) P&L attribution
into one auditable artifact written to data/pricer/risk/<valuation_date>/<run_id>.json,
with run/snapshot provenance so a risk number is reproducible from pinned inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.common import storage

from .backtest import BacktestResult
from .historical_var import FullRevalVarResult
from .jtd import JtdReport
from .limits import LimitBreach
from .pnl_explain import CarryThetaReport, PnlExplainReport
from .scenarios import ScenarioReport
from .sensitivities import RiskResult
from .var import VarResult


@dataclass
class RiskReport:
    valuation_date: str
    run_id: str
    snapshot_id: str                      # pricer market-data snapshot (pricing inputs)
    factor_snapshot_id: str = ""          # factor-history snapshot (VaR/scenario inputs)
    sensitivities: list[RiskResult] = field(default_factory=list)
    scenarios: list[ScenarioReport] = field(default_factory=list)
    var: VarResult | None = None
    full_reval_var: FullRevalVarResult | None = None
    pnl_explain: PnlExplainReport | None = None
    limit_breaches: list[LimitBreach] = field(default_factory=list)
    backtest: BacktestResult | None = None
    backtest_full_reval: BacktestResult | None = None
    carry_theta: CarryThetaReport | None = None
    jtd: JtdReport | None = None

    def book_sensitivities(self) -> dict[str, float]:
        agg: dict[str, float] = {}
        for rr in self.sensitivities:
            for f, v in rr.by_factor().items():
                agg[f] = agg.get(f, 0.0) + v
        return agg

    def to_dict(self) -> dict[str, Any]:
        return {
            "valuation_date": self.valuation_date,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "factor_snapshot_id": self.factor_snapshot_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "book_sensitivities": self.book_sensitivities(),
            "sensitivities": [r.to_dict() for r in self.sensitivities],
            "scenarios": [s.to_dict() for s in self.scenarios],
            "var": self.var.to_dict() if self.var else None,
            "full_reval_var": self.full_reval_var.to_dict() if self.full_reval_var else None,
            "pnl_explain": self.pnl_explain.to_dict() if self.pnl_explain else None,
            "limit_breaches": [b.as_dict() for b in self.limit_breaches],
            "backtest": self.backtest.to_dict() if self.backtest else None,
            "backtest_full_reval": self.backtest_full_reval.to_dict() if self.backtest_full_reval else None,
            "carry_theta": self.carry_theta.to_dict() if self.carry_theta else None,
            "jtd": self.jtd.to_dict() if self.jtd else None,
        }


def write_risk_report(report: RiskReport, project_root: Path) -> Path | str:
    out = storage.join(
        storage.data_base(project_root), "pricer", "risk", report.valuation_date,
        f"risk_{report.run_id}.json")
    storage.write_text_atomic(out, json.dumps(report.to_dict(), indent=2, default=str))
    return out if storage.is_remote_uri(out) else Path(out)
