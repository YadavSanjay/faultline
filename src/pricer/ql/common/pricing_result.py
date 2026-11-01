"""Result object returned by every pricer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class PricingResult:
    """Outcome of pricing a single trade.

    pv            : present value in trade currency (book sign convention).
    currency      : PV currency.
    valuation_date: as-of date.
    leg_pvs       : per-leg PV breakdown (e.g. {"fixed": .., "float": ..}).
    measures      : scalar risk/diagnostic measures (fair_rate, atm_rate, dv01,
                    vega, model params, cross-check prices, ...).
    diagnostics   : free-form notes (engine used, calibration error, warnings).
    warnings      : staleness / data-quality warnings propagated from the loader.
    provenance    : run/result lineage (run_id, git_sha, library versions, config
                    hash, market-data snapshot id) — stamped at write time. See
                    `src/pricer/provenance.py`.
    """
    trade_id: str
    trade_type: str
    currency: str
    valuation_date: date
    pv: float
    leg_pvs: dict[str, float] = field(default_factory=dict)
    measures: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "trade_type": self.trade_type,
            "currency": self.currency,
            "valuation_date": self.valuation_date.isoformat(),
            "pv": self.pv,
            "leg_pvs": self.leg_pvs,
            "measures": self.measures,
            "diagnostics": self.diagnostics,
            "warnings": self.warnings,
            "provenance": self.provenance,
        }
