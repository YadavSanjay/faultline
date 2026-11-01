"""Shared snapshot-selection + staleness logic for market-data adapters.

Picks the latest data snapshot on/before the valuation date and emits warnings
when the freshest available data is older than the configured tolerance, or when
no data exists on/before the valuation date.
"""

from __future__ import annotations

import pandas as pd

from ..input.common.quality import DataQualityError, QualityAction, default_policy
from ..input.common.valuation import ValuationContext


def pick_snapshot(
    as_of_series: pd.Series,
    ctx: ValuationContext,
    label: str = "market-data",
    kind: str | None = None,
) -> tuple[pd.Timestamp, list[str]]:
    """Return (chosen_snapshot_timestamp, warnings).

    Applies the run's data-quality policy for `kind`: a stale or missing snapshot is
    a warning under WARN/SUBSTITUTE, or raises `DataQualityError` under BLOCK so the
    trade fails fast with a clear message. With no policy attached (the default), every
    kind is WARN — identical to the pre-P0c behaviour.
    """
    policy = ctx.data_quality_policy or default_policy()
    action = policy.action_for(kind)
    tolerance = policy.tolerance_for(kind, ctx.max_data_staleness_days)

    warnings: list[str] = []
    vts = pd.Timestamp(ctx.valuation_date)
    on_or_before = as_of_series[as_of_series <= vts]

    if on_or_before.empty:
        chosen = as_of_series.min()
        msg = (
            f"No {label} snapshot on/before valuation date {ctx.valuation_date}; "
            f"using earliest available {chosen.date()}."
        )
        if action == QualityAction.BLOCK:
            raise DataQualityError(msg)
        warnings.append(msg)
        return chosen, warnings

    chosen = on_or_before.max()
    staleness = (vts - chosen).days
    if staleness > tolerance:
        msg = (
            f"{label} snapshot is stale: latest on/before {ctx.valuation_date} is "
            f"{chosen.date()} ({staleness}d old, tolerance {tolerance}d)."
        )
        if action == QualityAction.BLOCK:
            raise DataQualityError(msg)
        warnings.append(msg)
    return chosen, warnings
