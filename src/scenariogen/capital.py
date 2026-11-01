"""Parallel ES-under-shock capital across (scenario, tier) combos (opt-in via ``--workers``).

The heavy part of a full stress run is the ES-under-shock capital: one full-revaluation
historical-VaR pass (n_days x n_trades repricings) *per scenario x tier*. Two facts make it
much cheaper without changing any result:

  1. The **unshocked base ES is scenario-independent** — compute it once, reuse it (see
     ``stress.ScenarioStressEngine._capital``).
  2. Each combo's **shocked ES is independent** of the others, so they fan out over processes.

Each worker runs the *proven sequential* ``FullRevalVarEngine.historical_var`` for one combo,
rebuilding the shocked transforms in-process from the picklable factor ``vector`` (transforms
themselves are closures and don't pickle). No pricer-engine change, so a parallel result is
identical to the sequential one to the dollar — just faster. QuantLib's process-global
evaluation date is fine here: each worker is its own process.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from src.pricer.engine import PricingEngine
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk.historical_var import FullRevalVarEngine

from .factor_vector import vector_to_transforms

# Per-worker state (a module global — the picklable-free way to share an engine across
# ProcessPoolExecutor tasks; mirrors ``historical_var._HVAR_WORKER``).
_CAP_WORKER: dict[str, Any] = {}

# A single combo's shocked ES: (scenario_name, tier, factor_vector).
CapitalJob = tuple[str, str, dict[str, float]]


def _cap_worker_init(config: dict[str, Any], root: Path, ctx: ValuationContext,
                     lookback: int | None, book: str | None, asset_class: str | None) -> None:
    _CAP_WORKER.update(
        ve=FullRevalVarEngine(PricingEngine(config, root), config, root),
        ctx=ctx, lookback=lookback,
        trades=load_portfolio(asset_class=asset_class, book=book),
    )


def _cap_worker_task(job: CapitalJob) -> tuple[str, str, dict[str, float], int]:
    name, tier, vector = job
    w = _CAP_WORKER
    r = w["ve"].historical_var(w["trades"], w["ctx"], lookback=w["lookback"],
                               base_transforms=vector_to_transforms(vector))
    return name, tier, {k: float(v) for k, v in r.es.items()}, int(r.lookback_days)


def shocked_es_parallel(config: dict[str, Any], root: Path, ctx: ValuationContext,
                        lookback: int | None, book: str | None, asset_class: str | None,
                        jobs: list[CapitalJob], workers: int
                        ) -> dict[tuple[str, str], tuple[dict[str, float], int]]:
    """Fan the per-combo shocked ES out over ``workers`` processes.

    Returns ``{(scenario, tier): (es_by_level, lookback_days)}``. Each worker runs the same
    sequential full-reval the in-loop path would, so results are byte-identical — only the
    wall-clock changes. The worker re-loads the book by ``(book, asset_class)`` so it matches
    the trades the caller priced.
    """
    out: dict[tuple[str, str], tuple[dict[str, float], int]] = {}
    if not jobs:
        return out
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_cap_worker_init,
        initargs=(config, root, ctx, lookback, book, asset_class),
    ) as ex:
        for name, tier, es, lb in ex.map(_cap_worker_task, jobs):
            out[(name, tier)] = (es, lb)
    return out
