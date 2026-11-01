"""Pricing-module CLI driver.

Thin orchestration over `PricingEngine` (P1b): parse args, build the run context +
immutable snapshot, price the selected book (sequentially or across worker processes),
then persist results + the run summary + the data-quality report. All numerics live in
the engine, so this batch path and the HTTP service share one code path.

    python -m src.pricer.runner                                  # full portfolio
    python -m src.pricer.runner --asset-class rates
    python -m src.pricer.runner --trade TRADE_22
    python -m src.pricer.runner --valuation-date 2026-05-29 --valuation-type PV
    python -m src.pricer.runner --workers 4                      # parallel across processes
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# ---- path setup so the module runs from the project root ------------------
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# ---------------------------------------------------------------------------

from src.common import storage
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine, TradeOutcome
from src.pricer.input.common.quality import QualityPolicy
from src.pricer.input.common.trade import Trade
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.snapshot import MarketDataSnapshot
from src.pricer.obs import metrics
from src.pricer.obs.drift import detect_pv_drift
from src.pricer.obs.log import bind, configure_logging
from src.pricer.obs.run_manifest import RunManifest, RunManifestStore
from src.pricer.obs.tracing import drain_spans, observe_duration
from src.pricer.output.result_store import ResultStore
from src.pricer.output.result_writer import write_quality_report, write_result, write_summary
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.portfolio.versioning import PortfolioStore
from src.pricer.provenance import RunProvenance
from src.pricer.settings import get_settings

logger = logging.getLogger("pricer.runner")


def _setup_logging(json_logs: bool | None = None) -> None:
    log_dir = _ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    settings = get_settings()
    if json_logs is None:
        json_logs = settings.json_logs
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    configure_logging(json_logs=json_logs, level=level, logfile=log_dir / "pricer.log")


def _build_ctx(config: dict, valuation_date: date, valuation_type: str) -> ValuationContext:
    return ValuationContext(
        valuation_date=valuation_date,
        valuation_type=valuation_type,
        max_data_staleness_days=config.get("max_data_staleness_days", 7),
        data_quality_policy=QualityPolicy.from_config(config),
    )


def _summary_row(o: TradeOutcome) -> dict[str, Any]:
    return {
        "trade_id": o.trade_id,
        "asset_class": o.asset_class,
        "trade_type": o.trade_type,
        "currency": o.currency,
        "pv": o.result.pv if o.result is not None else "",
        "n_warnings": len(o.warnings) if o.result is not None else "",
        "warnings": o.warnings,
        "status": o.status,
    }


def _store_record(o: TradeOutcome, run_prov: RunProvenance, snapshot_id: str,
                  valuation_date: date, valuation_type: str) -> dict[str, Any]:
    return {
        "valuation_type": valuation_type,
        "valuation_date": valuation_date.isoformat(),
        "asset_class": o.asset_class,
        "trade_id": o.trade_id,
        "trade_type": o.trade_type,
        "currency": o.currency,
        "pv": float(o.result.pv) if o.result is not None else None,
        "status": o.status,
        "n_warnings": len(o.warnings),
        "run_id": run_prov.run_id,
        "snapshot_id": snapshot_id,
        "git_sha": run_prov.git_sha,
        "quantlib_version": run_prov.quantlib_version,
        "priced_at": run_prov.priced_at,
        "measures_json": json.dumps(o.result.measures) if o.result is not None else "{}",
    }


def _even_chunks(items: list[str], n: int) -> list[list[str]]:
    """Split `items` into <= n near-equal contiguous chunks (drops empties)."""
    n = max(1, min(n, len(items)))
    k, m = divmod(len(items), n)
    out = [items[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]
    return [c for c in out if c]


# --- parallel workers ------------------------------------------------------- #
# Each worker builds its own PricingEngine (loader + snapshot) once, then prices its
# chunk. State lives in a module global because that is the picklable-free way to share
# it across ProcessPoolExecutor tasks.
_WORKER: dict[str, Any] = {}


def _worker_init(config: dict, root: Path, valuation_date: date,
                 valuation_type: str, run_prov: RunProvenance) -> None:
    _WORKER.update(
        engine=PricingEngine(config, root),
        ctx=_build_ctx(config, valuation_date, valuation_type),
        run_prov=run_prov,
    )


def _worker_price_chunk(trade_ids: list[str]) -> list[TradeOutcome]:
    w = _WORKER
    by_id = {t.trade_id: t for t in load_portfolio()}
    trades = [by_id[tid] for tid in trade_ids if tid in by_id]
    outcomes: list[TradeOutcome] = w["engine"].price_portfolio(trades, w["ctx"], w["run_prov"])
    return outcomes


def run(
    asset_class: str | None = None,
    trade_id: str | None = None,
    valuation_date: date | None = None,
    valuation_type: str | None = None,
    workers: int = 1,
    book: str | None = None,
) -> list[dict]:
    """Price the selected book and persist every output under the project root (see `run_to`)."""
    return run_to(get_project_root(), asset_class=asset_class, trade_id=trade_id,
                  valuation_date=valuation_date, valuation_type=valuation_type,
                  workers=workers, book=book)


def run_to(
    output_root: Path,
    *,
    input_root: Path | None = None,
    asset_class: str | None = None,
    trade_id: str | None = None,
    valuation_date: date | None = None,
    valuation_type: str | None = None,
    workers: int = 1,
    book: str | None = None,
) -> list[dict]:
    """Price like `run`, reading inputs from `input_root` and writing every output under `output_root`.

    Market data, the market-data snapshot and git provenance come from `input_root` (default: the
    project root), resolved through `storage.data_base(input_root)`. Every file the run writes
    (per-trade PV JSON, summary, quality report, snapshot manifest, portfolio-version store, results
    and runs stores, run manifest and metrics, spans) goes under `storage.data_base(output_root)` at
    the same relative path `run` uses today. PV-drift reads prior marks from the output store.
    Nothing under `input_root` is created, modified or deleted.

    Args:
        output_root: Root whose data base receives every output.
        input_root: Root whose data base supplies market data; None means the project root.
        asset_class: Optional asset-class filter, as in `run`.
        trade_id: Optional single-trade filter, as in `run`.
        valuation_date: Valuation date; None means the portfolio default.
        valuation_type: Valuation type; None means the portfolio default.
        workers: Process workers for parallel pricing.
        book: Risk-appetite book name, as in `run`.

    Returns:
        The per-trade summary rows, as `run` returns them.
    """
    in_root = input_root if input_root is not None else get_project_root()
    out_root = output_root
    config = load_pricer_config()
    pf_cfg = load_portfolio_config(book=book)

    valuation_date = valuation_date or pf_cfg.default_valuation_date
    valuation_type = valuation_type or pf_cfg.default_valuation_type
    ctx = _build_ctx(config, valuation_date, valuation_type)

    run_prov = RunProvenance.capture(in_root, config)
    started_at, t0 = datetime.now(UTC).isoformat(), time.perf_counter()
    # Content-address + register the portfolio version; pin it into provenance (lineage).
    portfolio_version = PortfolioStore(out_root).register(pf_cfg.raw)
    run_prov = replace(run_prov, portfolio_version=portfolio_version)
    bind(run_id=run_prov.run_id, valuation_date=valuation_date.isoformat(),
         valuation_type=valuation_type, portfolio_version=portfolio_version)
    logger.info("run_id=%s git=%s QuantLib=%s config=%s portfolio=%s",
                run_prov.run_id, run_prov.git_sha, run_prov.quantlib_version,
                run_prov.config_hash, portfolio_version)

    with observe_duration(span="snapshot"):
        snapshot = MarketDataSnapshot.build(config, in_root)
        snapshot.write_manifest(out_root)
    bind(snapshot_id=snapshot.snapshot_id)
    logger.info("market-data snapshot_id=%s (%d input files, %d missing)",
                snapshot.snapshot_id, len(snapshot.files), len(snapshot.missing))

    with observe_duration(span="portfolio_load"):
        trades = load_portfolio(asset_class=asset_class, trade_id=trade_id, book=book)
    logger.info("Valuation date=%s type=%s | %d trade(s) selected (asset_class=%s, trade=%s, workers=%d)",
                valuation_date, valuation_type, len(trades), asset_class or "ALL",
                trade_id or "ALL", workers)
    if not trades:
        logger.warning("No trades selected — nothing to price.")
        return []

    with observe_duration(span="price_portfolio"):
        if workers and workers > 1 and len(trades) > 1:
            outcomes = _run_parallel(trades, config, in_root, valuation_date, valuation_type,
                                     run_prov, workers)
        else:
            engine = PricingEngine(config, in_root, snapshot=snapshot)
            outcomes = engine.price_portfolio(trades, ctx, run_prov)

    # PV-drift vs prior marks (P3) — query the store BEFORE this run is written to it.
    store = ResultStore(out_root)
    dcfg = config.get("observability", {}).get("pv_drift", {})
    with observe_duration(span="drift"):
        alerts = detect_pv_drift(outcomes, store, abs_tol=float(dcfg.get("abs_tol", 1000.0)),
                                 rel_tol=float(dcfg.get("rel_tol", 0.02)))
    for a in alerts:
        logger.warning("PV-DRIFT %s %s -> %s (%+.1f%%)", a.trade_id,
                       f"{a.prior_pv:,.0f}", f"{a.current_pv:,.0f}", 100.0 * a.rel_change)
        metrics.DATA_QUALITY.labels(severity="drift").inc()

    # Persistence (driver concern): per-trade JSON for priced trades + run-level files.
    summary_rows: list[dict] = []
    with observe_duration(span="persist"):
        for o in outcomes:
            if o.result is not None:
                write_result(o.result, o.asset_class, valuation_type, out_root)
            summary_rows.append(_summary_row(o))

        write_summary(summary_rows, valuation_type, valuation_date, out_root,
                      run_id=run_prov.run_id, snapshot_id=snapshot.snapshot_id)
        write_quality_report(summary_rows, valuation_type, valuation_date, out_root,
                             run_id=run_prov.run_id, snapshot_id=snapshot.snapshot_id,
                             policy=ctx.data_quality_policy.describe() if ctx.data_quality_policy else {})

        records = [_store_record(o, run_prov, snapshot.snapshot_id, valuation_date, valuation_type)
                   for o in outcomes]
        store.write_run(records, run_prov.run_id)

    _finalize_observability(outcomes, alerts, run_prov, snapshot, valuation_date,
                            valuation_type, started_at, time.perf_counter() - t0, out_root, config)
    _write_spans(out_root, run_prov.run_id, drain_spans(run_prov.run_id))

    logger.info("=" * 60)
    logger.info("Run complete. %d trade(s):", len(summary_rows))
    for r in summary_rows:
        pv = r["pv"]
        pv_str = f"{pv:,.2f}" if isinstance(pv, (int, float)) else str(pv)
        logger.info("  %-10s %-20s %15s  [%s]", r["trade_id"], r["trade_type"], pv_str, r["status"])
    return summary_rows


def _write_spans(root: Path, run_id: str, spans: list[dict]) -> None:
    """Persist the run's collected timing spans (ENG-4) for the UI run trace."""
    if not spans:
        return
    out = storage.join(storage.data_base(root), "pricer", "store", "spans", f"{run_id}.json")
    payload = {"module": "pricer", "run_id": run_id,
               "generated_at": datetime.now(UTC).isoformat(), "spans": spans}
    storage.write_text_atomic(out, json.dumps(payload, default=str))


def _finalize_observability(outcomes: list[TradeOutcome], alerts: list, run_prov: RunProvenance,
                            snapshot: MarketDataSnapshot, valuation_date: date, valuation_type: str,
                            started_at: str, duration_s: float, root: Path, config: dict) -> None:
    """Write the run manifest + run-level Prometheus metrics + textfile (P3)."""
    n_ok = sum(o.status == "ok" for o in outcomes)
    n_blocked = sum(o.status.startswith("BLOCKED") for o in outcomes)
    n_failed = sum(o.status.startswith("FAILED") for o in outcomes)
    n_warn = sum(len(o.warnings) for o in outcomes if o.status == "ok")
    total_pv = sum(o.result.pv for o in outcomes if o.result is not None)

    manifest = RunManifest(
        valuation_date=valuation_date.isoformat(), run_id=run_prov.run_id,
        valuation_type=valuation_type, snapshot_id=snapshot.snapshot_id,
        portfolio_version=run_prov.portfolio_version,
        git_sha=run_prov.git_sha, quantlib_version=run_prov.quantlib_version,
        started_at=started_at, finished_at=datetime.now(UTC).isoformat(),
        duration_s=round(duration_s, 3), n_trades=len(outcomes), n_ok=n_ok,
        n_blocked=n_blocked, n_failed=n_failed, n_warnings=n_warn,
        n_drift_alerts=len(alerts), total_pv=float(total_pv),
        status="ok" if n_failed == 0 and n_blocked == 0 else "degraded",
    )
    RunManifestStore(root).write(manifest)

    metrics.RUN_DURATION.observe(duration_s)
    metrics.BOOK_PV.labels(valuation_type=valuation_type).set(float(total_pv))
    metrics.LAST_RUN_TS.set(time.time())
    for status, n in [("ok", n_ok), ("blocked", n_blocked), ("failed", n_failed)]:
        metrics.RUN_TRADES.labels(status=status).set(n)
    rel = config.get("observability", {}).get("metrics_textfile", "logs/metrics/pricer.prom")
    try:
        metrics.write_textfile(root / rel)
    except Exception:  # metrics are best-effort, never fail a run on them
        logger.debug("metrics textfile write skipped")


def _run_parallel(trades: list[Trade], config: dict, root: Path, valuation_date: date,
                  valuation_type: str, run_prov: RunProvenance, workers: int) -> list[TradeOutcome]:
    """Price the book across `workers` processes, grouped for curve-cache locality."""
    ordered = sorted(trades, key=lambda t: (t.asset_class.value, t.trade_id))
    order = {t.trade_id: i for i, t in enumerate(ordered)}
    chunks = _even_chunks([t.trade_id for t in ordered], workers)
    logger.info("Parallel pricing: %d worker(s), %d chunk(s)", workers, len(chunks))

    outcomes: list[TradeOutcome] = []
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_worker_init,
        initargs=(config, root, valuation_date, valuation_type, run_prov),
    ) as ex:
        for chunk_outcomes in ex.map(_worker_price_chunk, chunks):
            outcomes.extend(chunk_outcomes)
    outcomes.sort(key=lambda o: order.get(o.trade_id, 0))
    return outcomes


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QuantLib pricing-module runner")
    p.add_argument("--asset-class", default=None, help="e.g. rates (default: full portfolio)")
    p.add_argument("--trade", dest="trade_id", default=None, help="single trade id, e.g. TRADE_22")
    p.add_argument("--valuation-date", default=None, help="ISO date; default: portfolio default")
    p.add_argument("--valuation-type", default=None, help="default: PV")
    p.add_argument("--workers", type=int, default=None,
                   help="process workers for parallel pricing (default: PRICER_WORKERS or 1)")
    p.add_argument("--book", default=None,
                   help="risk-appetite book to price: base|balanced|aggressive|risky "
                        "(default: the active default portfolio.json = aggressive; or PRICER_BOOK)")
    return p.parse_args()


if __name__ == "__main__":
    _setup_logging()
    args = _parse_args()
    workers = args.workers if args.workers is not None else get_settings().workers
    vdate = date.fromisoformat(args.valuation_date) if args.valuation_date else None
    run(
        asset_class=args.asset_class,
        trade_id=args.trade_id,
        valuation_date=vdate,
        valuation_type=args.valuation_type,
        workers=workers,
        book=args.book,
    )
