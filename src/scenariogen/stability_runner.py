"""Stability runner: run-to-run spread of the dual-RAG legs and of the P&L they produce (step 15c).

Resolves the dual-RAG services several times at the same ``as_of`` and reports how much each MacroRAG
and XbrlRag leg, and each scenario tier's P&L, moves between repetitions (``stability.py``). Everything
except the dual-RAG provider is built once and shared, so only the MacroRAG and XbrlRag answers can
differ between repetitions. Each repetition builds a fresh provider (fresh caches, a fresh XbrlRag
run budget, its own fail-loud preflight) whose MacroRAG calls send ``no_cache``, so MacroRAG re-runs
retrieval as well as synthesis. Tiers are materialised as the stress runner's dual-RAG path does and
fully revalued without capital, exit value, gate, sign-off, attribution or narrative. Numbers only:
no threshold is applied, and no forward-log predictions are written.

    python -m src.scenariogen.stability_runner                       # 3 repetitions, every tier
    python -m src.scenariogen.stability_runner --repetitions 5 --scenario ai_bust --severity mild

Writes ``data/pricer/scenario/<date>/stability_<run_id>.json`` and the call trace
``data/pricer/scenario/<date>/trace/<run_id>.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from src.common import storage
from src.common.obs.log import bind
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.provenance import RunProvenance
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.risk.historical_var import FullRevalVarEngine
from src.pricer.runner import _build_ctx, _setup_logging
from src.scenariogen.ablation import COMPOSITION_RULE
from src.scenariogen.anchoring import AnchorBook, SeverityConfig, option_anchors, severity_spec
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.config import load_geopolitical_config, load_plausibility_config, load_throughput_config
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.geopolitical import GeopoliticalProvider
from src.scenariogen.options import OptionSignalProvider
from src.scenariogen.options.sizing import OptionSizingConfig
from src.scenariogen.plausibility import hard_bound
from src.scenariogen.runner import (
    _build_dual_provider,
    _geo_for,
    _materialize,
    _materialize_anchored,
    _throughput_for,
)
from src.scenariogen.scenario_gen import ScenarioGenerator
from src.scenariogen.stability import leg_stability, pnl_stability
from src.scenariogen.stress import ScenarioStressEngine
from src.scenariogen.throughput import ThroughputProvider
from src.scenariogen.trace.capture import TraceCollector, bind_collector, new_collector, unbind_collector
from src.scenariogen.trace.store import write_trace

logger = logging.getLogger("scenariogen.stability")

DEFAULT_REPETITIONS = 3          # design §7.7: "runs three times at the same as_of"
MIN_REPETITIONS = 2              # one repetition has no spread
NOTE = "Run-to-run spread at one as_of; no threshold is applied."
SOURCES = ("macrorag", "xbrlrag", "xbrlrag_seed")
# Trace service names whose call latency ``latency_by_repetition`` sums (XbrlRag traces as "arag").
_LATENCY_SERVICES = {"macrorag": "macrorag", "xbrlrag": "arag"}


@dataclass
class StabilityRun:
    """What the repetition loop collected: the inputs of ``leg_stability`` / ``pnl_stability`` and more."""

    leg_records: list[dict[str, Any]] = field(default_factory=list)
    pnl_records: list[dict[str, Any]] = field(default_factory=list)
    resolutions: list[dict[str, Any]] = field(default_factory=list)
    stopped: list[dict[str, Any]] = field(default_factory=list)
    latency_by_repetition: list[dict[str, Any]] = field(default_factory=list)


def resolution_legs(inputs: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """The legs one dual-RAG resolution contributes, by source.

    Args:
        inputs: The scenario's ``DualRagEnrichmentProvider.layer_inputs``.

    Returns:
        ``{"macrorag": the drivers MacroRAG set (origin "macrorag"; {} when its legs were not applied),
        "xbrlrag": the fact-calibrated company legs, "xbrlrag_seed": their narrative seeds}``; both
        XbrlRag sources are {} without an XbrlRag resolution.
    """
    macro_res = inputs.get("macro_res")
    macro: dict[str, float] = {}
    if macro_res is not None and macro_res.used_macro:
        macro = {f: float(m) for f, m in macro_res.drivers.items()
                 if (macro_res.sources.get(f) or {}).get("origin") == "macrorag"}
    has_arag = inputs.get("arag_res") is not None
    calibrated = inputs.get("calibrated_legs") or {}
    seeds = inputs.get("seed_legs") or {}
    return {
        "macrorag": macro,
        "xbrlrag": {f: float(m) for f, m in calibrated.items()} if has_arag else {},
        "xbrlrag_seed": {f: float(m) for f, m in seeds.items()} if has_arag else {},
    }


def resolution_summary(repetition: int, name: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The report's per-repetition resolution entry: MacroRAG world view and fallback, XbrlRag use."""
    macro_res = inputs.get("macro_res")
    arag_res = inputs.get("arag_res")
    return {
        "repetition": repetition, "scenario": name,
        "world_view_id": (macro_res.world_view_id or None) if macro_res is not None else None,
        "used_macro": bool(macro_res is not None and macro_res.used_macro),
        "fallback_reason": macro_res.fallback_reason if macro_res is not None else None,
        "used_arag": bool(arag_res is not None and arag_res.used_arag),
    }


def _latency(repetition: int, records: Sequence[Any]) -> dict[str, Any]:
    """Total MacroRAG and XbrlRag call latency (seconds) and call counts of one repetition's records."""
    out: dict[str, Any] = {"repetition": repetition}
    for label, service in _LATENCY_SERVICES.items():
        calls = [r for r in records if r.service == service]
        out[f"{label}_s"] = sum(r.latency_ms for r in calls) / 1000.0
        out[f"n_{label}_calls"] = len(calls)
    return out


def run_repetitions(
    names: Sequence[str],
    tiers: Sequence[str],
    *,
    n_repetitions: int,
    build_provider: Callable[[int], DualRagEnrichmentProvider],
    materialize: Callable[[DualRagEnrichmentProvider, str, str], MaterializedScenario],
    price: Callable[[MaterializedScenario], Mapping[str, float]],
    collector: TraceCollector,
    check_scenario: Callable[[str], object] | None = None,
) -> StabilityRun:
    """Resolve and price every scenario tier ``n_repetitions`` times, each with a fresh provider.

    The provider is built (and preflighted) with the collector unbound, so health probes are not
    traced; the repetition's resolution and pricing run with ``collector`` bound and each call tagged
    with its scenario.

    Args:
        names: Scenario ids, in run order.
        tiers: Severity tiers to price per scenario.
        n_repetitions: Number of repetitions.
        build_provider: Builds a fresh dual-RAG provider for repetition ``r``.
        materialize: Materialises one tier from the repetition's provider.
        price: Full revaluation of a materialised scenario -> per-trade P&L.
        collector: The run's call-trace collector.
        check_scenario: Optional per-scenario check run before resolution (e.g. ``catalog.spec``, which
            raises for an uncovered replay window); a ``ScenarioGenerationError`` stops the scenario.

    Returns:
        The leg and P&L records, resolution summaries, stopped entries and per-repetition latency.

    Raises:
        ServiceUnavailableError: A required service failed its preflight (fail-loud, whole run).
        AragBudgetError: The repetition's XbrlRag budget was breached (fail-loud, whole run).
    """
    out = StabilityRun()
    for rep in range(n_repetitions):
        logger.info("Repetition %d/%d: fresh dual-RAG provider (MacroRAG no_cache)", rep + 1, n_repetitions)
        provider = build_provider(rep)
        start = len(collector.records)
        token = bind_collector(collector)
        try:
            for name in names:
                collector.set_scenario(name)
                try:
                    if check_scenario is not None:
                        check_scenario(name)
                    inputs = provider.layer_inputs(name)
                except ScenarioGenerationError as exc:          # fail-loud, this scenario and repetition
                    out.leg_records.append({"repetition": rep, "scenario": name, "stopped": True})
                    out.stopped.append({"repetition": rep, "scenario": name, "reason": exc.reason,
                                        "detail": exc.detail})
                    logger.error("  rep %d %-22s STOPPED (fail-loud): %s", rep, name, exc)
                    continue
                out.leg_records.append({"repetition": rep, "scenario": name, "legs": resolution_legs(inputs)})
                out.resolutions.append(resolution_summary(rep, name, inputs))
                for tier in tiers:
                    try:
                        ms = materialize(provider, name, tier)
                        pnl = price(ms)
                    except ScenarioGenerationError as exc:      # this tier stays unpriced in this repetition
                        out.stopped.append({"repetition": rep, "scenario": name, "severity": tier,
                                            "reason": exc.reason, "detail": exc.detail})
                        logger.error("  rep %d %-22s [%-7s] STOPPED (fail-loud): %s", rep, name, tier, exc)
                        continue
                    out.pnl_records.append({"repetition": rep, "scenario": name, "severity": tier,
                                            "total_pnl": float(sum(pnl.values()))})
        finally:
            unbind_collector(token)
        out.latency_by_repetition.append(_latency(rep, collector.records[start:]))
    return out


def report_payload(run: StabilityRun, collector: TraceCollector, *, valuation_date: str, run_id: str,
                   n_repetitions: int, severities: Sequence[str], provenance: Mapping[str, Any],
                   snapshot_id: str, factor_snapshot_id: str, covariance: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble the stability report from the repetition loop's records and the run's call trace.

    Args:
        run: What ``run_repetitions`` collected.
        collector: The run's call-trace collector (its summary becomes ``trace``).
        valuation_date: ISO valuation date.
        run_id: The run id.
        n_repetitions: Number of repetitions.
        severities: The tiers priced.
        provenance: ``RunProvenance.as_dict()``.
        snapshot_id: Market-data snapshot id.
        factor_snapshot_id: Factor-history snapshot id.
        covariance: The production covariance's ``as_dict()``.

    Returns:
        The report payload, with ``legs`` from ``leg_stability`` and ``pnl`` from ``pnl_stability``.
    """
    return {
        "valuation_date": valuation_date, "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(), "n_repetitions": n_repetitions, "no_cache": True,
        "severities": list(severities), "composition_rule": COMPOSITION_RULE,
        "provenance": dict(provenance), "snapshot_id": snapshot_id,
        "factor_snapshot_id": factor_snapshot_id, "covariance": dict(covariance),
        "resolutions": run.resolutions,
        "legs": leg_stability(run.leg_records, n_repetitions=n_repetitions, sources=SOURCES),
        "pnl": pnl_stability(run.pnl_records, n_repetitions=n_repetitions),
        "stopped": run.stopped, "trace": collector.summary(),
        "latency_by_repetition": run.latency_by_repetition, "note": NOTE,
    }


def summary_lines(legs: Sequence[Mapping[str, Any]], pnl: Sequence[Mapping[str, Any]],
                  sources: Sequence[str] = SOURCES) -> list[str]:
    """Log lines: one per scenario x tier (P&L min, max, range, priced count), one per scenario and source.

    The source line names the number of legs and the leg with the largest ``range`` (ties: first in
    sorted order).
    """
    def num(v: float | None) -> str:
        return "n/a" if v is None else f"{v:,.0f}"

    lines = [f"{r['scenario']:<22} [{r['severity']:<7}] P&L min {num(r['min'])} max {num(r['max'])} "
             f"range {num(r['range'])} priced {r['n_priced']}/{r['n_repetitions']}" for r in pnl]
    for row in legs:
        for source in sources:
            entries: Mapping[str, Any] = row.get(source) or {}
            if not entries:
                lines.append(f"{row['scenario']:<22} {source:<13} 0 leg(s)")
                continue
            top = max(sorted(entries), key=lambda k: entries[k]["range"] or 0.0)   # max keeps the first tie
            lines.append(f"{row['scenario']:<22} {source:<13} {len(entries)} leg(s); widest {top} "
                         f"range {entries[top]['range']:.6g}")
    return lines


def write_report(payload: Mapping[str, Any], root: Path) -> str:
    """Write the stability report to ``data/pricer/scenario/<date>/stability_<run_id>.json``."""
    out = storage.join(storage.data_base(root), "pricer", "scenario", str(payload["valuation_date"]),
                       f"stability_{payload['run_id']}.json")
    storage.write_text_atomic(out, json.dumps(payload, indent=2, default=str))
    return out


def run_stability(
    book: str | None = None,
    valuation_date: date | None = None,
    scenario: str | None = None,
    severities: Sequence[str] | None = None,
    cov_lookback: int | None = None,
    n_repetitions: int = DEFAULT_REPETITIONS,
) -> dict[str, Any]:
    """Run the dual-RAG resolution ``n_repetitions`` times at one as_of and write the stability report.

    Args:
        book: Portfolio book; default = the portfolio default.
        valuation_date: Valuation date; default = the portfolio default.
        scenario: One scenario id; default = every catalog scenario.
        severities: Tiers to price; default = every catalog tier.
        cov_lookback: Covariance lookback override.
        n_repetitions: Number of repetitions (at least 2).

    Returns:
        The report payload (also written to ``data/pricer/scenario/<date>/stability_<run_id>.json``).

    Raises:
        ValueError: ``n_repetitions`` below 2, or an unknown severity tier.
    """
    if n_repetitions < MIN_REPETITIONS:
        raise ValueError(f"n_repetitions must be at least {MIN_REPETITIONS}, got {n_repetitions}")
    root = get_project_root()
    config = load_pricer_config()
    pf_cfg = load_portfolio_config(book=book)
    valuation_date = valuation_date or pf_cfg.default_valuation_date
    ctx = _build_ctx(config, valuation_date, "PV")
    run_prov = RunProvenance.capture(root, config)
    bind(run_id=run_prov.run_id, valuation_date=valuation_date.isoformat(), module="scenariogen.stability")

    # --- built once, shared by every repetition ------------------------------------------------- #
    engine = PricingEngine(config, root)
    stress_engine = ScenarioStressEngine(engine, FullRevalVarEngine(engine, config, root))
    catalog = ScenarioCatalog.from_config(FactorHistory(root, config), lookback=cov_lookback,
                                          project_root=root)
    factor_snap = FactorHistory(root, config).snapshot()
    trades = load_portfolio(book=book)
    names = [scenario] if scenario else catalog.names()
    tiers = list(severities) if severities else catalog.tiers()
    unknown = [t for t in tiers if t not in catalog.severity_tiers]
    if unknown:
        raise ValueError(f"unknown severity {unknown}; expected one of {catalog.tiers()}")
    plaus_bounds = dict(load_plausibility_config(root).get("bounds", {}))
    replay_bounds = {f: b for f in catalog.covariance.factors if (b := hard_bound(plaus_bounds, f)) is not None}
    scen_cfg = catalog.scenario_config()                          # replays resolved from the episode
    scen_specs = scen_cfg["scenarios"]
    geo_provider = GeopoliticalProvider.load(root, load_geopolitical_config(root))
    thr_provider = ThroughputProvider.load(root, load_throughput_config(root))
    sev_cfg = SeverityConfig.load(root)
    anchor_book = (AnchorBook.load(root, sev_cfg)
                   if any(severity_spec(scen_specs.get(n, {})) for n in names) else None)
    sizing_cfg = OptionSizingConfig.load(root)
    market = OptionSignalProvider.load(root, None, as_of=valuation_date, sizing=sizing_cfg)

    # GPR x throughput multipliers, severity blocks and option anchors read local files: once per scenario.
    state_mult: dict[str, float] = {}
    sev_by_name: dict[str, dict[str, str] | None] = {}
    opt_by_name: dict[str, tuple[Any, Any]] = {}
    for name in names:
        spec = scen_specs.get(name, {})
        geo = _geo_for(name, spec, geo_provider, valuation_date)
        thr = _throughput_for(name, spec, thr_provider, valuation_date)
        state_mult[name] = ((geo.multiplier if geo is not None else 1.0)
                            * (thr.multiplier if thr is not None else 1.0))
        sev = severity_spec(spec)
        sev_by_name[name] = sev
        opt_by_name[name] = (option_anchors(sev["lead"], sev["direction"], market, sizing_cfg, sev_cfg,
                                            plaus_bounds)
                             if sev is not None and sev["source"] == "options" else (None, None))

    generators: dict[tuple[str, bool], ScenarioGenerator] = {}

    def generator_for(tier: str, name: str) -> ScenarioGenerator:
        cov = catalog.covariance_for(tier, name)            # crisis Σ for severe; may raise per scenario
        key = (tier, cov is catalog.covariance)
        if key not in generators:
            generators[key] = ScenarioGenerator(cov)
        return generators[key]

    def materialize(provider: DualRagEnrichmentProvider, name: str, tier: str) -> MaterializedScenario:
        gen = generator_for(tier, name)
        sev = sev_by_name[name]
        if sev is not None and anchor_book is not None:
            anchors, anchor_source = opt_by_name[name]
            ms = _materialize_anchored(name, tier, catalog, gen, provider, prov_key="dualrag",
                                       state_raw=state_mult[name], sev=sev, book=anchor_book, cfg=sev_cfg,
                                       bounds=replay_bounds if name in catalog.replays else None,
                                       anchors=anchors, anchor_source=anchor_source)
        else:
            ms = _materialize(name, tier, catalog, gen, provider, prov_key="dualrag",
                              severity_multiplier=state_mult[name])
        return catalog.stamp(name, ms)

    def price(ms: MaterializedScenario) -> dict[str, float]:
        res = stress_engine.run(ms, trades, ctx, with_capital=False)
        return {r.trade_id: r.pnl for r in res.results}

    def build_provider(_rep: int) -> DualRagEnrichmentProvider:
        return _build_dual_provider(root, valuation_date, names, scen_cfg, macro_no_cache=True)

    logger.info("Stability run %s | valuation_date=%s | %d repetition(s) | tiers=%s | %d trade(s) | cov=%s",
                run_prov.run_id, valuation_date, n_repetitions, ",".join(tiers), len(trades),
                catalog.covariance.cov_id)
    collector = new_collector(run_prov.run_id)
    run = run_repetitions(names, tiers, n_repetitions=n_repetitions, build_provider=build_provider,
                          materialize=materialize, price=price, collector=collector,
                          check_scenario=catalog.spec)        # an uncovered replay window stops the scenario
    trace_out = write_trace(collector, valuation_date.isoformat(), root)
    logger.info("Wrote call-trace (%d call(s)) -> %s", len(collector.records), trace_out)

    payload = report_payload(
        run, collector, valuation_date=valuation_date.isoformat(), run_id=run_prov.run_id,
        n_repetitions=n_repetitions, severities=tiers, provenance=run_prov.as_dict(),
        snapshot_id=engine.snapshot_id, factor_snapshot_id=factor_snap.snapshot_id,
        covariance=catalog.covariance.as_dict())
    for line in summary_lines(payload["legs"], payload["pnl"]):
        logger.info("  %s", line)
    out = write_report(payload, root)
    logger.info("Wrote stability report -> %s", out)
    return payload


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run-to-run stability of the dual-RAG named scenarios")
    p.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS,
                   help=f"repetitions at one as_of (default {DEFAULT_REPETITIONS}, at least {MIN_REPETITIONS})")
    p.add_argument("--scenario", default=None, help="one scenario id; default: all")
    p.add_argument("--severity", action="append", default=None,
                   help="tier to price (repeatable); default: every catalog tier")
    p.add_argument("--book", default=None, help="portfolio book (default: portfolio default)")
    p.add_argument("--valuation-date", default=None, help="ISO date; default: portfolio default")
    p.add_argument("--cov-lookback", type=int, default=None, help="covariance lookback days")
    args = p.parse_args(argv)
    if args.repetitions < MIN_REPETITIONS:
        p.error(f"--repetitions must be at least {MIN_REPETITIONS}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    _setup_logging()
    args = _parse_args(argv)
    vdate = date.fromisoformat(args.valuation_date) if args.valuation_date else None
    run_stability(book=args.book, valuation_date=vdate, scenario=args.scenario, severities=args.severity,
                  cov_lookback=args.cov_lookback, n_repetitions=args.repetitions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
