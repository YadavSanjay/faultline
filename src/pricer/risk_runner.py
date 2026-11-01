"""Risk-engine CLI driver (P2).

Computes the full risk picture for the selected book at a valuation date — per-trade
sensitivities, all configured stress scenarios, historical VaR/ES, and (optionally)
P&L attribution for one scenario — then persists one provenance-stamped risk report.

    python -m src.pricer.risk_runner
    python -m src.pricer.risk_runner --asset-class rates --no-var
    python -m src.pricer.risk_runner --explain taiwan_geopolitical
    python -m src.pricer.risk_runner --valuation-date 2026-05-29 --no-key-rates
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common import storage
from src.common.obs import metrics as obs_metrics
from src.common.obs.log import bind
from src.common.obs.run_manifest import RunManifest, RunManifestStore
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.provenance import RunProvenance
from src.pricer.risk.backtest import VarBacktester
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.risk.historical_var import FullRevalVarEngine
from src.pricer.risk.jtd import JtdEngine
from src.pricer.risk.limits import LimitMonitor
from src.pricer.risk.pnl_explain import PnlExplainEngine
from src.pricer.risk.report import RiskReport, write_risk_report
from src.pricer.risk.scenarios import ScenarioEngine, ScenarioReport, load_scenarios
from src.pricer.risk.sensitivities import RiskEngine
from src.pricer.risk.var import VarEngine
from src.pricer.runner import _build_ctx, _setup_logging
from src.pricer.settings import get_settings

logger = logging.getLogger("pricer.risk_runner")

# Operational metrics (shared registry; names prefixed `risk_`).
RISK_RUNS = obs_metrics.counter("risk", "runs_total", "Risk runs by status", ("status",))
RISK_DURATION = obs_metrics.histogram(
    "risk", "run_seconds", "Risk run duration (s)",
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
)
RISK_VAR = obs_metrics.gauge(
    "risk", "var_value", "Latest historical VaR by confidence level", ("level",))
RISK_SENS_FAILED = obs_metrics.counter(
    "risk", "sensitivities_failed_total", "Per-trade sensitivity failures")


def run_risk(
    asset_class: str | None = None,
    valuation_date: date | None = None,
    scenarios: bool = True,
    var: bool = True,
    full_reval_var: bool = True,
    explain: str | None = None,
    key_rates: bool = True,
    workers: int = 1,
    backtest: bool = True,
    book: str | None = None,
) -> RiskReport:
    root = get_project_root()
    config = load_pricer_config()
    pf_cfg = load_portfolio_config(book=book)
    valuation_date = valuation_date or pf_cfg.default_valuation_date
    ctx = _build_ctx(config, valuation_date, "PV")

    run_prov = RunProvenance.capture(root, config)
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    bind(run_id=run_prov.run_id, valuation_date=valuation_date.isoformat(), module="risk")
    engine = PricingEngine(config, root)
    risk = RiskEngine(engine, config)
    scen_engine = ScenarioEngine(engine)

    trades = load_portfolio(asset_class=asset_class, book=book)
    bind(snapshot_id=engine.snapshot_id)
    logger.info("Risk run %s | valuation_date=%s | %d trade(s) | snapshot=%s",
                run_prov.run_id, valuation_date, len(trades), engine.snapshot_id)

    report = RiskReport(valuation_date.isoformat(), run_prov.run_id, engine.snapshot_id)

    # Pin the factor-history inputs (VaR/scenario data) with a content-addressed id, so a
    # risk run is reproducible from the exact bytes it consumed and a later CSV edit is detectable.
    factor_snap = FactorHistory(root, config).snapshot()
    factor_snap.write_manifest(storage.join(storage.data_base(root), "risk", "snapshots"))
    report.factor_snapshot_id = factor_snap.snapshot_id
    bind(factor_snapshot_id=factor_snap.snapshot_id)
    logger.info("factor snapshot_id=%s (%d input files, %d missing)",
                factor_snap.snapshot_id, len(factor_snap.files), len(factor_snap.missing))

    # 1. Sensitivities (Greeks)
    n_sens_failed = 0
    for trade in trades:
        try:
            report.sensitivities.append(risk.sensitivities(trade, ctx, key_rates=key_rates))
        except Exception as exc:
            n_sens_failed += 1
            RISK_SENS_FAILED.inc()
            logger.warning("  sensitivities failed for %s: %s", trade.trade_id, exc)
    _log_book_greeks(report.book_sensitivities())

    # 2. Stress scenarios (full revaluation)
    if scenarios:
        for name, scenario in load_scenarios(config).items():
            results = scen_engine.run(scenario, trades, ctx)
            sr = ScenarioReport(name, scenario.description, valuation_date, results)
            report.scenarios.append(sr)
            logger.info("  scenario %-22s total P&L = %s", name, f"{sr.total_pnl:,.0f}")

    # 3. VaR / Expected Shortfall (sensitivity-based historical simulation)
    if var:
        var_engine = VarEngine(risk, config, root)
        report.var = var_engine.historical_var(trades, ctx)
        for lvl, v in report.var.var.items():
            logger.info("  VaR(sens) %-5s = %15s   ES = %15s",
                        lvl, f"{v:,.0f}", f"{report.var.es.get(lvl, 0.0):,.0f}")
        if report.var.missing_factors:
            logger.warning("  VaR has %d factor(s) with exposure but no data: %s",
                           len(report.var.missing_factors), report.var.missing_factors)
        # 3a. VaR backtest (rolling exception count, Kupiec, traffic light)
        if backtest:
            report.backtest = VarBacktester(var_engine, config).run(trades, ctx)

    # 3c. Book risk-limit monitoring (VaR + aggregate sensitivities vs configured limits)
    report.limit_breaches = LimitMonitor(config).check(report.var, report.book_sensitivities())
    for b in report.limit_breaches:
        logger.warning("  LIMIT BREACH %-12s %s = %s > limit %s",
                       b.kind, b.name, f"{b.value:,.0f}", f"{b.limit:,.0f}")

    # 3b. Full-revaluation historical VaR (heavy: n_days x n_trades repricings)
    if full_reval_var:
        report.full_reval_var = FullRevalVarEngine(engine, config, root).historical_var(
            trades, ctx, workers=workers)
        fr = report.full_reval_var
        for lvl, v in fr.var.items():
            logger.info("  VaR(full-reval) %-5s = %15s   ES = %15s",
                        lvl, f"{v:,.0f}", f"{fr.es.get(lvl, 0.0):,.0f}")
        logger.info("  full-reval worst day %s P&L=%s over %d scenarios",
                    fr.worst_day, f"{fr.worst_pnl:,.0f}", fr.n_scenarios)
        # Backtest the full-reval P&L too (not just the linear model, Tier 2.6).
        if backtest and fr.pnl_vector:
            bt_cfg = config.get("risk", {}).get("backtest", {})
            import numpy as _np

            from src.pricer.risk.backtest import backtest_pnl
            report.backtest_full_reval = backtest_pnl(
                _np.asarray(fr.pnl_vector, dtype=float),
                level=float(bt_cfg.get("level", 0.99)),
                window=int(bt_cfg.get("window", 250)),
                test_days=int(bt_cfg.get("test_days", 250)),
                method="rolling_full_reval",
                dates=fr.dates or None,
            )

    # 4. P&L attribution for one scenario (optional)
    if explain:
        scen = load_scenarios(config).get(explain)
        if scen is None:
            logger.warning("  --explain: unknown scenario %r", explain)
        else:
            report.pnl_explain = PnlExplainEngine(risk, scen_engine).explain_scenario(
                scen, trades, ctx)
            logger.info("  P&L explain[%s]: actual=%s explained-coverage=%.1f%%",
                        explain, f"{report.pnl_explain.actual_total:,.0f}",
                        100.0 * report.pnl_explain.coverage)

    # 5. Carry / theta attribution — predicted 1-day P&L from time passing, market held flat.
    report.carry_theta = PnlExplainEngine(risk, scen_engine).carry_theta(trades, ctx)
    logger.info("  carry/theta (%dd): total = %s", report.carry_theta.horizon_days,
                f"{report.carry_theta.total_carry:,.0f}")

    # 6. Jump-to-default / incremental default risk add-on (credit + bond book).
    report.jtd = JtdEngine(config).compute(trades, valuation_date.isoformat())
    worst = report.jtd.worst
    logger.info("  JTD/IDR: idr = %s | %d issuer(s) | worst %s = %s",
                f"{report.jtd.idr:,.0f}", len(report.jtd.issuers),
                worst.issuer if worst else "-", f"{worst.jtd:,.0f}" if worst else "-")

    out = write_risk_report(report, root)
    logger.info("Wrote risk report -> %s", out)

    _record_run(root, run_prov, valuation_date, started, t0, trades, report, n_sens_failed)
    return report


def _record_run(root: Path, run_prov: RunProvenance, valuation_date: date,
                started: datetime, t0: float, trades: list,
                report: RiskReport, n_sens_failed: int) -> None:
    """Emit run metrics + one audit manifest row to data/risk/store/runs/."""
    duration = time.perf_counter() - t0
    status = "ok" if n_sens_failed == 0 else "partial"
    var_by_level = {str(lvl): float(v) for lvl, v in report.var.var.items()} if report.var else {}

    RISK_DURATION.observe(duration)
    RISK_RUNS.labels(status=status).inc()
    for lvl, v in var_by_level.items():
        RISK_VAR.labels(level=lvl).set(v)

    manifest = RunManifest(
        module="risk", run_id=run_prov.run_id,
        started_at=started.isoformat(), finished_at=datetime.now(UTC).isoformat(),
        duration_s=duration, status=status, partition=valuation_date.isoformat(),
        git_sha=run_prov.git_sha, config_hash=run_prov.config_hash,
        counts={"n_trades": len(trades), "n_sensitivities": len(report.sensitivities),
                "n_sens_failed": n_sens_failed, "n_scenarios": len(report.scenarios),
                "n_limit_breaches": len(report.limit_breaches)},
        metrics={f"var_{lvl}": v for lvl, v in var_by_level.items()},
        extra={"snapshot_id": report.snapshot_id,
               "factor_snapshot_id": report.factor_snapshot_id,
               "backtest_zone": report.backtest.zone if report.backtest else ""},
    )
    RunManifestStore(root, "risk").write(manifest)


def _log_book_greeks(greeks: dict[str, float]) -> None:
    headline = ["IR_PARALLEL", "CS01", "CS01:IG", "CS01:HY", "VEGA_RATES", "VEGA_EQUITY",
                "EQUITY_DELTA", "EQUITY_GAMMA", "FX_DELTA"]
    logger.info("Book sensitivities:")
    for f in headline:
        if f in greeks:
            logger.info("  %-14s %18s", f, f"{greeks[f]:,.1f}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QuantLib risk-engine runner")
    p.add_argument("--asset-class", default=None)
    p.add_argument("--valuation-date", default=None, help="ISO date; default: portfolio default")
    p.add_argument("--no-scenarios", action="store_true")
    p.add_argument("--no-var", action="store_true")
    p.add_argument("--no-full-reval-var", action="store_true",
                   help="skip full-revaluation historical VaR (the headline measure; heavy: "
                        "n_days x n_trades repricings — use --workers to parallelize)")
    p.add_argument("--no-key-rates", action="store_true", help="skip key-rate buckets (faster)")
    p.add_argument("--explain", default=None, help="scenario name for P&L attribution")
    p.add_argument("--workers", type=int, default=None,
                   help="process workers for full-reval VaR (default: PRICER_WORKERS or 1)")
    p.add_argument("--no-backtest", action="store_true", help="skip the VaR backtest")
    p.add_argument("--book", default=None,
                   help="risk-appetite book: base|balanced|aggressive|risky "
                        "(default: portfolio.json = aggressive; or PRICER_BOOK)")
    return p.parse_args()


if __name__ == "__main__":
    _setup_logging()
    args = _parse_args()
    vdate = date.fromisoformat(args.valuation_date) if args.valuation_date else None
    workers = args.workers if args.workers is not None else get_settings().workers
    run_risk(
        asset_class=args.asset_class,
        valuation_date=vdate,
        scenarios=not args.no_scenarios,
        var=not args.no_var,
        full_reval_var=not args.no_full_reval_var,
        explain=args.explain,
        key_rates=not args.no_key_rates,
        workers=workers,
        backtest=not args.no_backtest,
        book=args.book,
    )
