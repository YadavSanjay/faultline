"""Re-value the book (PV, Risk, Capital) under each profile's sizing.

Scaling a trade's position by multiplier ``m`` scales its PV and its scenario P&L exactly
linearly (``m`` identical contracts = ``m`` x the P&L, gamma included), so we price the
base book once and reweight per profile — no re-pricing per profile. The one non-linear
measure, ES/VaR economic capital, is taken from the reweighted P&L matrix the optimizer
already solved (``SizingResult.achieved_es975`` / ``achieved_var99``).

Two capital lenses are reported per profile:
  * economic capital = 97.5% ES of the reweighted book (FRTB tail measure);
  * stress capital   = worst loss across the configured stress scenarios (linear, exact).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..input.common.trade import Trade
from ..input.common.valuation import ValuationContext
from ..risk.scenarios import ScenarioEngine, load_scenarios
from .optimizer import SizingResult

logger = logging.getLogger("pricer.sizing.revaluation")


@dataclass
class Revaluation:
    """PV / Risk / Capital for one sized book (a profile, or the current book)."""

    label: str
    total_pv: float
    gross_notional: float
    net_notional: float
    var99: float
    es975: float
    economic_capital: float                       # = es975 (FRTB tail capital)
    stress_pnl: dict[str, float] = field(default_factory=dict)   # scenario -> book P&L
    worst_stress_scenario: str = ""
    worst_stress_loss: float = 0.0                # positive number = loss

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "total_pv": self.total_pv,
            "gross_notional": self.gross_notional,
            "net_notional": self.net_notional,
            "var99": self.var99,
            "es975": self.es975,
            "economic_capital": self.economic_capital,
            "stress_pnl": self.stress_pnl,
            "worst_stress_scenario": self.worst_stress_scenario,
            "worst_stress_loss": self.worst_stress_loss,
        }


def scenario_pnl_matrix(engine: Any, trades: list[Trade], ctx: ValuationContext,
                        config: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Per-trade P&L for each configured stress scenario: {scenario: {trade_id: pnl}}.

    Prices the base book once per scenario (shared across all profiles).
    """
    scen_engine = ScenarioEngine(engine)
    out: dict[str, dict[str, float]] = {}
    for name, scen in load_scenarios(config).items():
        results = scen_engine.run(scen, trades, ctx)
        out[name] = {r.trade_id: float(r.pnl) for r in results}
        logger.info("  scenario %-22s base P&L = %s",
                    name, f"{sum(out[name].values()):,.0f}")
    return out


def revalue(label: str, multipliers: dict[str, float], pv_by_id: dict[str, float],
            notional_by_id: dict[str, float], scen_pnl: dict[str, dict[str, float]],
            var99: float, es975: float) -> Revaluation:
    """Scale the base per-trade PV / scenario P&L by ``multipliers`` and aggregate."""
    total_pv = sum(multipliers.get(t, 0.0) * p for t, p in pv_by_id.items())
    gross = sum(abs(multipliers.get(t, 0.0) * n) for t, n in notional_by_id.items())
    net = sum(multipliers.get(t, 0.0) * n for t, n in notional_by_id.items())

    stress: dict[str, float] = {}
    for name, book in scen_pnl.items():
        stress[name] = sum(multipliers.get(t, 0.0) * pnl for t, pnl in book.items())

    worst_scen, worst_loss = "", 0.0
    if stress:
        worst_scen = min(stress, key=lambda k: stress[k])
        worst_loss = -min(stress.values())        # loss reported positive

    return Revaluation(
        label=label, total_pv=total_pv, gross_notional=gross, net_notional=net,
        var99=var99, es975=es975, economic_capital=es975,
        stress_pnl=stress, worst_stress_scenario=worst_scen, worst_stress_loss=worst_loss,
    )


def revalue_profiles(results: dict[str, SizingResult], pv_by_id: dict[str, float],
                     notional_by_id: dict[str, float],
                     scen_pnl: dict[str, dict[str, float]],
                     base_var99: float, base_es975: float) -> dict[str, Revaluation]:
    """Re-value the current book (m=1) plus each profile's sized book."""
    out: dict[str, Revaluation] = {}
    unit = dict.fromkeys(pv_by_id, 1.0)
    out["current"] = revalue("Current book", unit, pv_by_id, notional_by_id,
                             scen_pnl, base_var99, base_es975)
    for key, res in results.items():
        mult = {s.trade_id: s.multiplier for s in res.sizes}
        out[key] = revalue(res.label, mult, pv_by_id, notional_by_id, scen_pnl,
                          res.achieved_var99, res.achieved_es975)
    return out
