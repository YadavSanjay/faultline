"""Layer-ablation runner: what each enrichment layer does to a dual-RAG named scenario.

Resolves MacroRAG, XbrlRag, news, crowding, GPR and throughput **once** per scenario (the same
fail-loud preflight and gates as ``runner --dual-rag``), then rebuilds every layer subset from those
cached inputs (``ablation.compose_definition``), fully revalues each distinct definition (no
capital, no narrative) and reports the ladder, the exact Shapley values, per-subset per-trade P&L
and the factors at the return floor. The ablation measures influence, not value. Its own service calls
are traced, and ``layer_cost`` sets each layer's calls, latency and reported cost next to its Shapley
contribution (cost-per-layer).

    python -m src.scenariogen.ablation_runner                       # all scenarios, mild tier
    python -m src.scenariogen.ablation_runner --scenario ai_bust --severity adverse

Writes ``data/pricer/scenario/<date>/ablation_<run_id>.json`` and the call trace
``data/pricer/scenario/<date>/trace/<run_id>.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import combinations
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
from src.scenariogen import factor_keys as fk
from src.scenariogen.ablation import COMPOSITION_RULE, LAYERS, ladder_attribution, shapley_attribution
from src.scenariogen.anchoring import (
    ANCHORED_RULE,
    AnchorBook,
    SeverityConfig,
    anchor_tier,
    leg_transform,
    option_anchors,
    severity_spec,
)
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.config import load_geopolitical_config, load_plausibility_config, load_throughput_config
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.forward_log import (
    PredictionLog,
    horizon_scale,
    is_pit_clean,
    live_triggers,
    prediction_record,
)
from src.scenariogen.geopolitical import GeopoliticalProvider
from src.scenariogen.layer_cost import layer_usage, layer_value
from src.scenariogen.options import OptionSignalProvider
from src.scenariogen.options.sizing import OptionSizingConfig
from src.scenariogen.plausibility import hard_bound
from src.scenariogen.runner import _build_dual_provider, _event_hits, _geo_for, _throughput_for
from src.scenariogen.scenario_gen import ScenarioDefinition, ScenarioGenerator
from src.scenariogen.stress import ScenarioStressEngine
from src.scenariogen.throughput import ThroughputProvider
from src.scenariogen.trace.capture import trace_run
from src.scenariogen.trace.store import write_trace

logger = logging.getLogger("scenariogen.ablation")

DEFAULT_TIER = "mild"
# A factor is "at the floor" when its move is within this distance of RETURN_FLOOR.
_FLOOR_TOL = 1e-9


def floor_hits(vector: Mapping[str, float]) -> list[str]:
    """Equity/FX factors whose materialized move sits at ``factor_keys.RETURN_FLOOR``."""
    return sorted(k for k, v in vector.items()
                  if (fk.is_equity(k) or fk.is_fx(k)) and v <= fk.RETURN_FLOOR + _FLOOR_TOL)


def _definition_key(drivers: Mapping[str, float], overlays: Mapping[str, float], scale: float) -> str:
    return json.dumps([sorted(drivers.items()), sorted(overlays.items()), scale])


def ablate_scenario(
    name: str,
    tier: str,
    *,
    provider: DualRagEnrichmentProvider,
    generator: ScenarioGenerator,
    price: Callable[[MaterializedScenario], dict[str, float]],
    tier_scale: float,
    state_mult: float,
    order: Sequence[str] = LAYERS,
    anchoring: AnchorContext | None = None,
) -> dict[str, Any]:
    """Ablate one scenario tier over every subset of ``order``.

    Args:
        name: Scenario id.
        tier: Severity tier label.
        provider: Dual-RAG provider; its services are resolved once and cached.
        generator: Materializes a definition into a full factor vector.
        price: Full revaluation of a materialized scenario -> per-trade P&L.
        tier_scale: The tier's scale from ``scenarios.json``.
        state_mult: GPR x throughput multiplier (applied only when ``"state"`` is enabled).
        order: Ladder order; also the Shapley players.
        anchoring: For an anchored scenario, its lead/direction, anchors and config: each subset is
            composed at tier scale 1 and rescaled onto the tier's anchor (rule ``dualrag-v2``).

    Returns:
        The scenario's ablation record: ladder, Shapley values and per-subset results.

    Raises:
        ScenarioGenerationError: A service failed fail-loud, or a subset has no drivers.
    """
    priced: dict[str, dict[str, Any]] = {}        # definition key -> priced result (deduplicated)
    subsets: dict[frozenset[str], dict[str, Any]] = {}

    def evaluate(layers: frozenset[str]) -> dict[str, Any]:
        if layers in subsets:
            return subsets[layers]
        if anchoring is None:
            defn, _ = provider.definition_for(name, severity=tier, severity_scale=tier_scale,
                                              layers=layers, state_mult=state_mult)
            ms_pre: MaterializedScenario | None = None
        else:
            defn, ms_pre = _anchored_subset(name, tier, layers, provider=provider, generator=generator,
                                            state_mult=state_mult, ctx=anchoring)
        key = (_definition_key(defn.drivers, defn.overlays, defn.severity_scale) if ms_pre is None
               else json.dumps(sorted(ms_pre.vector.items())))
        if key not in priced:
            ms = ms_pre if ms_pre is not None else generator.materialize(defn)
            pnl = price(ms)
            priced[key] = {"pnl_by_trade": pnl, "total_pnl": float(sum(pnl.values())),
                           "floor_hits": floor_hits(ms.vector),
                           "drivers": dict(defn.drivers), "overlays": dict(defn.overlays),
                           "severity_scale": defn.severity_scale, "vector": dict(ms.vector),
                           "horizon_days": defn.horizon_days, "scenario_version": defn.version,
                           "anchoring": ms.provenance.get("severity")}
        subsets[layers] = priced[key]
        return priced[key]

    ladder = ladder_attribution(lambda s: evaluate(s)["pnl_by_trade"], order)
    shapley = shapley_attribution(lambda s: evaluate(s)["total_pnl"], order)
    for k in range(len(order) + 1):                 # Shapley visits every subset; make sure of it
        for combo in combinations(order, k):
            evaluate(frozenset(combo))

    _, full = provider.definition_for(name, severity=tier, severity_scale=tier_scale,
                                      layers=frozenset(order), state_mult=state_mult)
    world_view = full.macro.world_view_id if full.macro is not None else None
    return {
        "scenario": name, "severity": tier, "tier_scale": tier_scale, "state_mult": state_mult,
        "composition_rule": ANCHORED_RULE if anchoring is not None else COMPOSITION_RULE,
        "world_view_id": world_view, "order": list(order),
        "ladder": ladder, "shapley": shapley,
        "n_subsets": len(subsets), "n_priced": len(priced),
        "subsets": [{"layers": sorted(s), **r} for s, r in
                    sorted(subsets.items(), key=lambda kv: (len(kv[0]), sorted(kv[0])))],
        "resolutions": full.to_dict(),
    }


@dataclass(frozen=True)
class AnchorContext:
    """An anchored scenario's lead factor, direction, anchor book and severity config."""

    lead: str
    direction: str
    book: AnchorBook
    cfg: SeverityConfig
    bounds: Mapping[str, float] | None = None    # hard bounds capping a replay's scale; None = no cap
    anchors: Mapping[str, float] | None = None   # option-implied tiers replacing history (``source: options``)
    anchor_source: Mapping[str, Any] | None = None


def _anchored_subset(name: str, tier: str, layers: frozenset[str], *, provider: DualRagEnrichmentProvider,
                     generator: ScenarioGenerator, state_mult: float, ctx: AnchorContext,
                     ) -> tuple[ScenarioDefinition, MaterializedScenario]:
    """Compose one layer subset at tier scale 1 and rescale it onto the tier's anchor."""
    state_used = max(state_mult, ctx.cfg.state_floor)
    record: dict[str, Any] = {}
    hook = leg_transform(generator, ctx.cfg.leg_cap, record, lead=ctx.lead)
    composed, _ = provider.definition_for(name, severity=tier, severity_scale=1.0, layers=layers,
                                          state_mult=state_used, leg_transform=hook)
    authored, _ = provider.definition_for(name, severity=tier, severity_scale=1.0, layers=frozenset())
    ms = anchor_tier(name, tier, lead=ctx.lead, direction=ctx.direction, composed=composed,
                     authored=authored, generator=generator, book=ctx.book, cfg=ctx.cfg,
                     state_raw=state_mult, state_used=state_used, legs=record, bounds=ctx.bounds,
                     anchors=ctx.anchors, anchor_source=ctx.anchor_source)
    return composed, ms


def run_ablation(
    book: str | None = None,
    valuation_date: date | None = None,
    scenario: str | None = None,
    severity: str = DEFAULT_TIER,
    cov_lookback: int | None = None,
) -> dict[str, Any]:
    """Run the layer ablation over the named scenarios and write the report.

    Args:
        book: Portfolio book; default = the portfolio default.
        valuation_date: Valuation date; default = the portfolio default.
        scenario: One scenario id; default = every catalog scenario.
        severity: Tier to ablate (default ``mild``, where fewer factors sit at the floor).
        cov_lookback: Covariance lookback override.

    Returns:
        The report payload (also written to ``data/pricer/scenario/<date>/ablation_<run_id>.json``).
    """
    root = get_project_root()
    config = load_pricer_config()
    pf_cfg = load_portfolio_config(book=book)
    valuation_date = valuation_date or pf_cfg.default_valuation_date
    ctx = _build_ctx(config, valuation_date, "PV")
    run_prov = RunProvenance.capture(root, config)
    bind(run_id=run_prov.run_id, valuation_date=valuation_date.isoformat(), module="scenariogen.ablation")

    engine = PricingEngine(config, root)
    stress_engine = ScenarioStressEngine(engine, FullRevalVarEngine(engine, config, root))
    catalog = ScenarioCatalog.from_config(FactorHistory(root, config), lookback=cov_lookback,
                                          project_root=root)
    factor_snap = FactorHistory(root, config).snapshot()
    trades = load_portfolio(book=book)
    names = [scenario] if scenario else catalog.names()
    if severity not in catalog.severity_tiers:
        raise ValueError(f"unknown severity {severity!r}; expected one of {catalog.tiers()}")
    plaus_bounds = dict(load_plausibility_config(root).get("bounds", {}))
    replay_bounds = {f: b for f in catalog.covariance.factors if (b := hard_bound(plaus_bounds, f)) is not None}

    scen_cfg = catalog.scenario_config()                          # replays resolved from the episode
    provider = _build_dual_provider(root, valuation_date, names, scen_cfg)   # fail-loud preflight
    geo_provider = GeopoliticalProvider.load(root, load_geopolitical_config(root))
    thr_provider = ThroughputProvider.load(root, load_throughput_config(root))
    scen_specs = scen_cfg["scenarios"]
    sev_cfg = SeverityConfig.load(root)
    anchor_book = (AnchorBook.load(root, sev_cfg)
            if any(severity_spec(scen_specs.get(n, {})) for n in names) else None)
    sizing_cfg = OptionSizingConfig.load(root)
    market = OptionSignalProvider.load(root, None, as_of=valuation_date, sizing=sizing_cfg)

    def price(ms: MaterializedScenario) -> dict[str, float]:
        res = stress_engine.run(ms, trades, ctx, with_capital=False)
        return {r.trade_id: r.pnl for r in res.results}

    logger.info("Ablation run %s | valuation_date=%s | tier=%s | %d trade(s) | cov=%s | rule=%s",
                run_prov.run_id, valuation_date, severity, len(trades), catalog.covariance.cov_id,
                COMPOSITION_RULE)
    records: list[dict[str, Any]] = []
    stopped: list[dict[str, Any]] = []
    pred_rows: list[dict[str, Any]] = []
    # Trace this run's service calls (cost-per-layer); the preflight probes above are not captured.
    with trace_run(run_prov.run_id) as collector:
        for name in names:
            collector.set_scenario(name)
            spec = scen_specs.get(name, {})
            geo = _geo_for(name, spec, geo_provider, valuation_date)
            thr = _throughput_for(name, spec, thr_provider, valuation_date)
            state_mult = (geo.multiplier if geo is not None else 1.0) * (thr.multiplier if thr is not None else 1.0)
            sev = severity_spec(spec)
            opt_anchors, opt_source = (option_anchors(sev["lead"], sev["direction"], market, sizing_cfg, sev_cfg,
                                                      plaus_bounds)
                                       if sev is not None and sev["source"] == "options" else (None, None))
            anchor_ctx = (AnchorContext(sev["lead"], sev["direction"], anchor_book, sev_cfg,
                                        bounds=replay_bounds if name in catalog.replays else None,
                                        anchors=opt_anchors, anchor_source=opt_source)
                   if sev is not None and anchor_book is not None else None)
            try:
                catalog.spec(name)                      # an uncovered replay window stops the scenario
                # Crisis Σ for the severe tier (fail-loud per scenario) unless severe_sigma is "production".
                generator = catalog.generator_for(severity, name)
                rec = ablate_scenario(name, severity, provider=provider, generator=generator, price=price,
                                      tier_scale=catalog.severity_tiers[severity], state_mult=state_mult,
                                      anchoring=anchor_ctx)
            except ScenarioGenerationError as exc:
                stopped.append({"scenario": name, "reason": exc.reason, "detail": exc.detail})
                logger.error("  %-22s STOPPED (fail-loud): %s", name, exc)
                continue
            records.append(rec)
            pred_rows.extend(prediction_rows(
                rec, run_id=run_prov.run_id, as_of=valuation_date.isoformat(), covariance=catalog.covariance,
                snapshot_id=engine.snapshot_id,
                triggers=live_triggers(geopolitical=geo.to_dict() if geo is not None else None,
                                       throughput=thr.to_dict() if thr is not None else None,
                                       news=provider.news, crowding=provider.crowding,
                                       event_hits=_event_hits(provider, name))))
            last = rec["ladder"][-1]
            logger.info("  %-22s authored=%15s full=%15s priced %d/%d subsets | shapley %s",
                        name, f"{rec['ladder'][0]['total_pnl']:,.0f}", f"{last['total_pnl']:,.0f}",
                        rec["n_priced"], rec["n_subsets"],
                        {k: f"{v:,.0f}" for k, v in rec["shapley"].items() if abs(v) > 0.5})
    trace_out = write_trace(collector, valuation_date.isoformat(), root)
    layer_cost = layer_value(layer_usage([r.to_dict() for r in collector.records]), records)
    logger.info("Wrote call-trace (%d call(s)) -> %s", len(collector.records), trace_out)
    for line in layer_cost_lines(layer_cost):
        logger.info("  %s", line)

    payload: dict[str, Any] = {
        "valuation_date": valuation_date.isoformat(), "run_id": run_prov.run_id,
        "generated_at": datetime.now(UTC).isoformat(), "severity": severity,
        "composition_rule": COMPOSITION_RULE, "order": list(LAYERS),
        "provenance": run_prov.as_dict(), "snapshot_id": engine.snapshot_id,
        "factor_snapshot_id": factor_snap.snapshot_id, "covariance": catalog.covariance.as_dict(),
        "note": "Measures each layer's influence on the scenario, not its value.",
        "scenarios": records, "stopped": stopped,
        "trace": collector.summary(), "layer_cost": layer_cost,
    }
    out = storage.join(storage.data_base(root), "pricer", "scenario", valuation_date.isoformat(),
                       f"ablation_{run_prov.run_id}.json")
    storage.write_text_atomic(out, json.dumps(payload, indent=2, default=str))
    logger.info("Wrote ablation report -> %s", out)
    PredictionLog(root).write(pred_rows, run_prov.run_id)
    logger.info("Logged %d prediction(s) for forward scoring", len(pred_rows))
    return payload


def layer_cost_lines(block: Mapping[str, Any]) -> list[str]:
    """One log line per layer of a ``layer_cost`` block's totals, then one for the overhead calls."""
    def cost(v: float | None) -> str:
        return "not reported" if v is None else f"${v:,.4f}"

    lines = [f"layer {t['severity']:<7} {t['layer']:<18} |shapley| {t['contribution']:>15,.0f}  "
             f"{t['n_calls']:>4} call(s) {t['latency_s']:>8.1f}s  cost {cost(t['cost_usd'])}"
             + (f"  {t['contribution_per_s']:,.0f}/s" if t["contribution_per_s"] is not None else "")
             for t in block.get("totals", [])]
    o = block.get("overhead", {})
    lines.append(f"overhead {o.get('n_calls', 0)} call(s) {float(o.get('latency_s', 0.0)):.1f}s  "
                 f"cost {cost(o.get('cost_usd'))}")
    return lines


def prediction_rows(rec: Mapping[str, Any], *, run_id: str, as_of: str, covariance: FactorCovariance,
                    snapshot_id: str, triggers: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Forward-log rows for one ablated scenario: one per layer subset (identical definitions repeat).

    Every subset is logged, not just the distinct definitions, so a layer's with/without pair always
    exists for ``forward_log.layer_value``.
    """
    rows: list[dict[str, Any]] = []
    for sub in rec["subsets"]:
        horizon = int(sub["horizon_days"])
        rows.append(prediction_record(
            run_id=run_id, scenario=rec["scenario"], severity=rec["severity"], layers=sub["layers"],
            as_of=as_of, horizon_days=horizon, vector=sub["vector"],
            scale=horizon_scale(covariance, horizon, sub["vector"]),
            scenario_version=str(sub["scenario_version"]), cov_id=covariance.cov_id,
            snapshot_id=snapshot_id, composition_rule=str(rec.get("composition_rule", COMPOSITION_RULE)),
            triggers=triggers,
            pit_clean=is_pit_clean(as_of, datetime.now(UTC)), source="ablation"))
    return rows


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Layer ablation of the dual-RAG named scenarios")
    p.add_argument("--book", default=None, help="portfolio book (default: portfolio default)")
    p.add_argument("--valuation-date", default=None, help="ISO date; default: portfolio default")
    p.add_argument("--scenario", default=None, help="one scenario id; default: all")
    p.add_argument("--severity", default=DEFAULT_TIER, help="tier to ablate (default: mild)")
    p.add_argument("--cov-lookback", type=int, default=None, help="covariance lookback days")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _setup_logging()
    args = _parse_args(argv)
    vdate = date.fromisoformat(args.valuation_date) if args.valuation_date else None
    run_ablation(book=args.book, valuation_date=vdate, scenario=args.scenario,
                 severity=args.severity, cov_lookback=args.cov_lookback)
    return 0


if __name__ == "__main__":
    sys.exit(main())


