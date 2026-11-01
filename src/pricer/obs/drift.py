"""PV-drift detection (P3).

After pricing, compare each trade's PV to its most recent stored mark (the P1c results
store) and raise an alert when the move breaches both an absolute and a relative
tolerance — the "page me when a price jumps" control that catches data or model breakage
before it flows downstream. Both tolerances must trip, so tiny notionals and huge
notionals are each handled sensibly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..engine import TradeOutcome
from ..output.result_store import ResultStore


@dataclass
class DriftAlert:
    trade_id: str
    asset_class: str
    prior_pv: float
    current_pv: float
    abs_change: float
    rel_change: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id, "asset_class": self.asset_class,
            "prior_pv": self.prior_pv, "current_pv": self.current_pv,
            "abs_change": self.abs_change, "rel_change": self.rel_change,
        }


def detect_pv_drift(outcomes: list[TradeOutcome], store: ResultStore,
                    abs_tol: float = 1000.0, rel_tol: float = 0.02) -> list[DriftAlert]:
    """Flag trades whose PV moved > abs_tol AND > rel_tol vs the last stored mark.

    Call this BEFORE writing the current run to the store, so the comparison is against
    the prior run. Trades with no history (first ever pricing) never alert.
    """
    alerts: list[DriftAlert] = []
    for o in outcomes:
        if o.result is None:
            continue
        hist = store.pv_history(o.trade_id)
        if hist.empty:
            continue
        prior = float(hist.iloc[-1]["pv"])
        current = float(o.result.pv)
        abs_change = current - prior
        rel_change = abs_change / prior if prior else float("inf")
        if abs(abs_change) > abs_tol and abs(rel_change) > rel_tol:
            alerts.append(DriftAlert(o.trade_id, o.asset_class, prior, current,
                                     abs_change, rel_change))
    return alerts
