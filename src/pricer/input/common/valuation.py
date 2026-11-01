"""Valuation context — the 'when / what type' of a pricing run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .quality import QualityPolicy


@dataclass(frozen=True)
class ValuationContext:
    """Everything a pricer needs to know about the run, independent of the trade.

    valuation_date : the curve/as-of date (QL evaluationDate).
    base_currency  : reporting currency for the book.
    valuation_type : "PV" today; "STRESS_*" later. Drives the output path.
    max_data_staleness_days : baseline staleness tolerance (days). The loader warns
                              — or, under a BLOCK policy, refuses — when the latest
                              market-data snapshot is older than this before
                              valuation_date.
    data_quality_policy : per-kind warn/block/substitute policy (P0c). None falls
                          back to a permissive WARN-everything policy.
    """
    valuation_date: date
    base_currency: str = "USD"
    valuation_type: str = "PV"
    max_data_staleness_days: int = 7
    data_quality_policy: QualityPolicy | None = None
