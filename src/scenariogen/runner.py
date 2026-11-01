"""Scenario stress-test CLI driver (S2).

Materializes the named scenarios at their severity tiers, fully reprices the book under
each (P&L), computes ES-under-shock capital, and writes one provenance-stamped stress
report.

    python -m src.scenariogen.runner
    python -m src.scenariogen.runner --scenario taiwan_geopolitical --severity severe
    python -m src.scenariogen.runner --asset-class equity --no-capital
    python -m src.scenariogen.runner --valuation-date 2026-05-29 --var-lookback 60
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common import storage
from src.common.obs import metrics as obs_metrics
from src.common.obs.log import bind
from src.common.obs.run_manifest import RunManifest, RunManifestStore
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.cash_equity.instruments import CashEquityTrade
from src.pricer.input.equity.instruments import EquityOptionTrade
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.provenance import RunProvenance
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.risk.historical_var import FullRevalVarEngine
from src.pricer.runner import _build_ctx, _setup_logging
from src.scenariogen.ablation import COMPOSITION_RULE, LAYERS
from src.scenariogen.anchoring import (
    ANCHORED_RULE,
    AnchorBook,
    SeverityConfig,
    anchor_tier,
    leg_transform,
    option_anchors,
    severity_spec,
)
from src.scenariogen.arag.budget import AragBudget
from src.scenariogen.arag.client import AragClient, RestTransport
from src.scenariogen.arag.provider import AragCompanyProvider
from src.scenariogen.attribution import ScenarioAttribution, build_attribution
from src.scenariogen.capital import shocked_es_parallel
from src.scenariogen.catalog import CRISIS_UNAVAILABLE, ScenarioCatalog
from src.scenariogen.collateral import CollateralEstimator
from src.scenariogen.config import (
    load_arag_config,
    load_crowding_config,
    load_geopolitical_config,
    load_limits_config,
    load_macro_config,
    load_news_config,
    load_plausibility_config,
    load_scenario_config,
    load_throughput_config,
)
from src.scenariogen.crowding import CrowdingProvider
from src.scenariogen.dashboard import ScenarioResultStore, StressDashboard, stress_rows
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.events import EventsConfig, ScenarioEvents, load_events
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.forward_log import (
    PredictionLog,
    horizon_scale,
    is_pit_clean,
    live_triggers,
    prediction_record,
)
from src.scenariogen.geopolitical import GeopoliticalProvider, GeoResolution
from src.scenariogen.liquidity import LiquidityAdjuster, trade_liq_meta
from src.scenariogen.macro.client import MacroRAGClient, RequestsTransport
from src.scenariogen.macro.provider import (
    FALLBACK_FILTER_INVALID,
    FALLBACK_GATE_FAILED,
    FALLBACK_NOT_CONFIGURED,
    FALLBACK_SYNTHESIS_FAILED,
    FALLBACK_UNAVAILABLE,
    MacroScenarioProvider,
)
from src.scenariogen.narrative import AttributionNarrator, build_narrator
from src.scenariogen.news.provider import NewsEventProvider
from src.scenariogen.options import OptionSignalProvider
from src.scenariogen.options.sizing import OptionSizingConfig, exceeds_option_tail
from src.scenariogen.plausibility import PlausibilityChecker, hard_bound
from src.scenariogen.preflight import preflight_services
from src.scenariogen.report import (
    ReverseStressReport,
    StressReport,
    held_out_entry,
    held_out_summary,
    tier_order_flags,
    write_reverse_stress_report,
    write_stress_report,
)
from src.scenariogen.reverse import ReverseStressResult, ReverseStressSolver
from src.scenariogen.scenario_gen import ScenarioGenerator
from src.scenariogen.signal_health import (
    STATUS_GAUGE,
    Observation,
    SignalHealthConfig,
    build_signal_health,
    crowding_observation,
    macro_observation,
    news_observation,
    options_observation,
    problem_lines,
    state_observation,
    xbrlrag_observation,
)
from src.scenariogen.signoff import SignoffStore
from src.scenariogen.stress import (
    DEFAULT_CAPITAL_LEVEL,
    ScenarioCapital,
    ScenarioStressEngine,
    ScenarioStressResult,
)
from src.scenariogen.throughput import ThroughputProvider, ThroughputResolution
from src.scenariogen.trace.capture import (
    TraceCollector,
    bind_collector,
    new_collector,
    unbind_collector,
)
from src.scenariogen.trace.store import write_trace
from src.scenariogen.versioning import ScenarioStore

logger = logging.getLogger("scenariogen.runner")

# Operational metrics (shared registry; names prefixed `scenariogen_`).
SCEN_RUNS = obs_metrics.counter("scenariogen", "runs_total", "Stress runs by status", ("status",))
SCEN_DURATION = obs_metrics.histogram(
    "scenariogen", "run_seconds", "Stress run duration (s)",
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
)
SCEN_SCENARIOS = obs_metrics.counter(
    "scenariogen", "scenarios_total", "Scenario x tier outcomes", ("outcome",))
SCEN_LIMIT_BREACH = obs_metrics.counter(
    "scenariogen", "limit_breach_total", "Scenario limit breaches")
SCEN_SIGNAL = obs_metrics.gauge(
    "scenariogen", "signal_status", "Last stress run's source status (0 ok, 1 degraded, 2 down, -1 unused)",
    ("source",))
# Stop reasons of a scenario whose required MacroRAG legs were missing (dual-RAG, fail-loud).
_MACRO_STOPS = frozenset({FALLBACK_NOT_CONFIGURED, FALLBACK_SYNTHESIS_FAILED, FALLBACK_UNAVAILABLE,
                          FALLBACK_GATE_FAILED, FALLBACK_FILTER_INVALID})


def _macro_client(conn: dict, *, timeout_s: float) -> MacroRAGClient:
    api_key = os.environ.get(str(conn.get("api_key_env", "")))
    transport = RequestsTransport(
        str(conn.get("base_url", "")), api_key=api_key, timeout_s=timeout_s,
        max_attempts=int(conn.get("max_attempts", 2)),
        backoff_s=float(conn.get("backoff_s", 1.0)),
        backoff_max_s=float(conn.get("backoff_max_s", 16.0)),
        breaker_threshold=int(conn.get("breaker_threshold", 5)))
    return MacroRAGClient(transport)


def _build_macro_provider(root: Path, as_of: date,
                          scenario_config: dict[str, Any] | None = None) -> MacroScenarioProvider | None:
    """Construct a MacroRAG-backed driver provider when the macro service is enabled.

    ``scenario_config`` is the catalog's view with replays resolved (``ScenarioCatalog.scenario_config``).
    """
    cfg = load_macro_config(root)
    conn = cfg.get("connection", {})
    if not conn.get("enabled", False):
        logger.info("  macro: disabled in config -> static drivers")
        return None
    # Startup liveness probe on a SHORT-timeout client: if MacroRAG is unreachable, run on
    # static drivers rather than retry-storming the dead service once per scenario. The data
    # client uses the generous timeout — mode=synthesize runs a server-side LLM pipeline that
    # can take minutes (~4 min observed), far past a health-probe budget.
    try:
        _macro_client(conn, timeout_s=float(conn.get("health_timeout_s", 20))).healthz()
    except Exception as exc:
        logger.warning("  macro: healthz probe failed (%s) -> static drivers", exc)
        return None
    data_client = _macro_client(conn, timeout_s=float(conn.get("timeout_s", 600)))
    return MacroScenarioProvider(cfg, as_of=as_of, client=data_client, project_root=root,
                                 scenario_config=scenario_config)


def _arag_budget(conn: dict) -> AragBudget | None:
    """Run-level ARag breaker from config (connection.budget); None -> unlimited (F5)."""
    b = conn.get("budget") or {}
    max_calls = b.get("max_total_calls")
    max_wall = b.get("max_total_wall_clock_s")
    if max_calls is None and max_wall is None:
        return None
    return AragBudget(
        max_calls=int(max_calls) if max_calls is not None else None,
        max_wall_clock_s=float(max_wall) if max_wall is not None else None)


def _arag_client(conn: dict, *, timeout_s: float, budget: AragBudget | None = None,
                 as_of: date | None = None) -> AragClient:
    api_key = os.environ.get(str(conn.get("api_key_env", "")))
    transport = RestTransport(
        str(conn.get("base_url", "")), api_key=api_key, timeout_s=timeout_s,
        max_attempts=int(conn.get("max_attempts", 2)),
        backoff_s=float(conn.get("backoff_s", 2.0)),
        backoff_max_s=float(conn.get("backoff_max_s", 30.0)),
        breaker_threshold=int(conn.get("breaker_threshold", 3)), budget=budget)
    return AragClient(transport, as_of=as_of.isoformat() if as_of is not None else None)


def _build_dual_provider(root: Path, as_of: date, names: list[str],
                         scenario_config: dict[str, Any] | None = None, *,
                         arag_config: dict[str, Any] | None = None,
                         arag_as_of: date | None = None,
                         macro_no_cache: bool = False) -> DualRagEnrichmentProvider:
    """Build the dual-RAG provider (MacroRAG + ARag) and preflight both, fail-loud.

    Unions the ``required_services`` of the scenarios about to run and probes MacroRAG/ARag
    liveness with short-timeout health clients; a required service that is down aborts the run
    here (``ServiceUnavailableError``) rather than proceeding with no service input. The heavy
    data calls use generous (10-min) transports built separately. ``scenario_config`` is the
    catalog's view with replays resolved, shared by all three providers. ``macro_no_cache``
    (stability runs) makes every MacroRAG synthesis call bypass MacroRAG's answer caches; the
    health client never synthesises.
    """
    macro_cfg = load_macro_config(root)
    # ``arag_config`` / ``arag_as_of`` (event replay): point-in-time fiscal years and fact reads.
    arag_cfg = arag_config if arag_config is not None else load_arag_config(root)
    macro_conn = macro_cfg.get("connection", {})
    arag_conn = arag_cfg.get("connection", {})

    required: set[str] = set()
    for name in names:
        for s in arag_cfg.get("scenarios", {}).get(name, {}).get("required_services", []):
            required.add(str(s).lower())

    # Data clients (generous 10-min timeouts) drive the providers; mode=synthesize runs a
    # server-side LLM pipeline that can take minutes ...
    macro_provider = None
    macro_health: MacroRAGClient | None = None
    if macro_conn.get("enabled", False):
        macro_provider = MacroScenarioProvider(
            macro_cfg, as_of=as_of,
            client=_macro_client(macro_conn, timeout_s=float(macro_conn.get("timeout_s", 600))),
            project_root=root, scenario_config=scenario_config, no_cache=macro_no_cache)
        macro_health = _macro_client(macro_conn, timeout_s=float(macro_conn.get("health_timeout_s", 20)))

    arag_provider = None
    arag_health: AragClient | None = None
    if arag_conn.get("enabled", False):
        data_timeout = float(arag_conn.get("timeout_s", 600))
        # One run-level budget shared by the data client only (F5); the health probe is exempt.
        arag_provider = AragCompanyProvider(
            arag_cfg, as_of=as_of,
            client=_arag_client(arag_conn, timeout_s=data_timeout, budget=_arag_budget(arag_conn),
                                as_of=arag_as_of),
            project_root=root, scenario_config=scenario_config)
        health_timeout = float(arag_conn.get("health_timeout_s", 15))
        arag_health = _arag_client(arag_conn, timeout_s=health_timeout)

    # ... but liveness is probed with short-timeout clients so a dead service fails the
    # preflight quickly instead of blocking on the 10-min data timeout.
    preflight_services(required, macro_client=macro_health, arag_client=arag_health)

    # A3: resolve the news feed once, point-in-time to the run's as_of, and share it across
    # scenarios/tiers. It scales the ARag idiosyncratic legs (mode-(ii)); absent feed / held
    # gate -> a no-op, so a run without news is byte-identical.
    news_resolution = None
    news_provider = NewsEventProvider.load(root, load_news_config(root))
    if news_provider is not None:
        news_resolution = news_provider.resolve(as_of)

    # F5: crowding (FINRA days-to-cover) composes with news on the ARag idiosyncratic legs.
    crowding_resolution = None
    crowding_provider = CrowdingProvider.load(root, load_crowding_config(root))
    if crowding_provider is not None:
        crowding_resolution = crowding_provider.resolve(as_of)

    # Step 9: theme-relevant news/crowding on every single-name equity driver + 8-K trigger hits
    # (events.json, composition rule dualrag-v3). No file -> the legs-only dualrag-v2 behaviour.
    all_names = list((scenario_config or load_scenario_config(root)).get("scenarios", {}))
    events_cfg = load_events(root, all_names)
    events = (ScenarioEvents(events_cfg, news_resolution, crowding_resolution, as_of)
              if events_cfg is not None else None)

    return DualRagEnrichmentProvider(
        macro_provider=macro_provider, arag_provider=arag_provider,
        news_resolution=news_resolution, crowding_resolution=crowding_resolution, project_root=root,
        scenario_config=scenario_config, events=events)


def run_stress(
    asset_class: str | None = None,
    book: str | None = None,
    valuation_date: date | None = None,
    scenario: str | None = None,
    severity: str | None = None,
    with_capital: bool = True,
    var_lookback: int | None = None,
    cov_lookback: int | None = None,
    gate: bool = True,
    macro: bool = False,
    dual_rag: bool = False,
    narrative: bool = True,
    require_signoff: bool = False,
    exit_value: bool = True,
    workers: int | None = None,
    sensitivities: bool = False,
) -> StressReport:
    root = get_project_root()
    config = load_pricer_config()
    pf_cfg = load_portfolio_config(book=book)
    valuation_date = valuation_date or pf_cfg.default_valuation_date
    ctx = _build_ctx(config, valuation_date, "PV")

    run_prov = RunProvenance.capture(root, config)
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    bind(run_id=run_prov.run_id, valuation_date=valuation_date.isoformat(), module="scenariogen")
    engine = PricingEngine(config, root)
    var_engine = FullRevalVarEngine(engine, config, root)
    stress_engine = ScenarioStressEngine(engine, var_engine)

    catalog = ScenarioCatalog.from_config(FactorHistory(root, config), lookback=cov_lookback,
                                          project_root=root)
    # Pin the factor-history inputs (covariance + full-reval data) for reproducibility/lineage.
    factor_snap = FactorHistory(root, config).snapshot()
    factor_snap.write_manifest(storage.join(storage.data_base(root), "scenariogen", "snapshots"))
    bind(snapshot_id=engine.snapshot_id, factor_snapshot_id=factor_snap.snapshot_id)
    generator = ScenarioGenerator(catalog.covariance)
    plaus_cfg = load_plausibility_config(root)
    # B4: wire the option-implied signals into the plausibility gate when they exist on disk. The
    # market-implied check is additive and flag-only (provider is None -> the check is a no-op).
    # options-shock-sizing: point-in-time to the valuation date (latest snapshot <= as_of within the
    # staleness limit, vol-index fallback); the same provider sizes option-anchored tiers.
    sizing_cfg = OptionSizingConfig.load(root)
    events_cfg = load_events(root, list(load_scenario_config(root).get("scenarios", {})))
    etf_tickers = events_cfg.excluded if events_cfg is not None else frozenset()
    market = OptionSignalProvider.load(root, plaus_cfg.get("market_implied"), as_of=valuation_date,
                                       sizing=sizing_cfg)
    if market is not None:
        logger.info("plausibility: option-implied market check enabled (%d names)", len(market.names()))
    checker = PlausibilityChecker(catalog.covariance, plaus_cfg, market=market)
    # Each tier materializes and is gated with its own Σ: production for mild/adverse, crisis Σ for
    # severe (crisis-sigma-tiers) unless the scenario's severity block sets severe_sigma "production"
    # (issuer-scenario-support). Built lazily so an unbuildable crisis Σ stops only severe tiers.
    tier_tools: dict[tuple[str, bool], tuple[ScenarioGenerator, PlausibilityChecker]] = {}

    def tools_for(tier: str, name: str) -> tuple[ScenarioGenerator, PlausibilityChecker]:
        cov = catalog.covariance_for(tier, name)                # may raise ScenarioGenerationError
        key = (tier, cov is catalog.covariance)
        if key not in tier_tools:
            tier_tools[key] = (ScenarioGenerator(cov) if cov is not catalog.covariance else generator,
                               PlausibilityChecker(cov, plaus_cfg, market=market)
                               if cov is not catalog.covariance else checker)
        return tier_tools[key]

    # A replay's anchored scale is capped at the hard plausibility bounds (its higher tiers are the
    # episode as large as stays plausible).
    replay_bounds = {f: b for f in catalog.covariance.factors
                     if (b := hard_bound(dict(plaus_cfg.get("bounds", {})), f)) is not None}
    store = ScenarioStore(root)
    signoff = SignoffStore(root)
    trades = load_portfolio(asset_class=asset_class, book=book)

    # P4 exit-value + funding post-processing (mode-(iii); additive/reversible). Built once and
    # applied to each materialized result; each degrades to a no-op when its config is disabled
    # or absent, so a run with --no-exit-value is byte-identical to the pre-P4 report.
    liq_adjuster: LiquidityAdjuster | None = None
    col_estimator: CollateralEstimator | None = None
    liq_meta: dict[str, Any] = {}
    if exit_value:
        liq_adjuster = LiquidityAdjuster.build(root)
        col_estimator = CollateralEstimator.build(root)
        if liq_adjuster is not None or col_estimator is not None:
            liq_meta = trade_liq_meta(trades)

    # F8/F9: external-index severity multipliers + triggers for tagged scenarios (P7), resolved
    # once per scenario (PIT to the valuation date), applied to the whole shock (mode-(ii)):
    #   geopolitical (GPR) -> taiwan_geopolitical ; throughput (AI-capex) -> ai_bust.
    geo_provider = GeopoliticalProvider.load(root, load_geopolitical_config(root))
    thr_provider = ThroughputProvider.load(root, load_throughput_config(root))
    scen_specs = catalog.scenario_config()["scenarios"]            # replays resolved
    # Severity anchoring: scenarios with a "severity" block get their tiers from history (long-history
    # anchors), with the enrichment shaping them within a band. Loaded once, only if any is anchored.
    sev_cfg = SeverityConfig.load(root)
    anchor_book = (AnchorBook.load(root, sev_cfg)
            if any(severity_spec(scen_specs.get(n, {})) for n in catalog.names()) else None)

    # The sensitivity library runs only on request (--sensitivities); --scenario may name one.
    names = ([scenario] if scenario
             else catalog.names() + (catalog.sensitivity_names() if sensitivities else []))
    tiers = [severity] if severity else catalog.tiers()

    # Provider selection: dual-RAG (MacroRAG + ARag, fail-loud preflight) supersedes the
    # macro-only path. The dual builder preflights the required services and raises up front
    # if any is down.
    prov_key = "macro"
    provider: MacroScenarioProvider | DualRagEnrichmentProvider | None
    narrator: AttributionNarrator | None = None
    if dual_rag:
        provider = _build_dual_provider(root, valuation_date, names, catalog.scenario_config())
        prov_key = "dualrag"
        narrator = build_narrator(root) if narrative else None
    else:
        provider = (_build_macro_provider(root, valuation_date, catalog.scenario_config())
                    if macro else None)

    # Value-add attribution is computed once per scenario (unscaled contributions are tier-
    # independent) and reused across its severity tiers.
    attribution_cache: dict[str, ScenarioAttribution] = {}

    # Bind a call-trace collector so every MacroRAG/ARag/LLM call in the loop is captured
    # (P3). Health probes in _build_dual_provider ran before this and are intentionally skipped.
    collector: TraceCollector | None = None
    trace_token = None
    if dual_rag:
        collector = new_collector(run_prov.run_id)
        trace_token = bind_collector(collector)

    logger.info("Stress run %s | valuation_date=%s | %d trade(s) | snapshot=%s | cov=%s | mode=%s",
                run_prov.run_id, valuation_date, len(trades), engine.snapshot_id,
                catalog.covariance.cov_id, "dual-rag" if dual_rag else ("macro" if macro else "static"))

    # Capital (ES-under-shock) is the heavy measure: a full-reval hist-VaR per scenario x tier.
    # The unshocked base ES is scenario-independent, so compute it ONCE and reuse it (Fix 1 —
    # avoids recomputing the same base on every combo). With --workers>1 the per-combo *shocked*
    # passes fan out over processes via capital.shocked_es_parallel (Fix 2; results identical).
    base_capital = (var_engine.historical_var(trades, ctx, lookback=var_lookback)
                    if with_capital else None)
    n_workers = int(workers or 1)
    parallel_capital = with_capital and n_workers > 1
    cap_jobs: list[tuple[str, str, dict[str, float]]] = []
    cap_pending: list[tuple[str, str, str, ScenarioStressResult]] = []
    option_flags: list[dict[str, Any]] = []         # option_anchor_unavailable / below_option_tail

    report = StressReport(valuation_date.isoformat(), run_prov.run_id, engine.snapshot_id,
                          covariance=catalog.covariance.as_dict())
    n_held_signoff = n_held_gate = n_stopped = 0
    # Forward scoring log: every materialized tier is recorded as a prediction, scored once its
    # horizon has passed (forward_score). The enabled layers say which enrichment produced it.
    pred_layers = (list(LAYERS) if dual_rag else ["macrorag", "state"] if macro else ["state"])
    pred_rule = COMPOSITION_RULE if dual_rag else ("macro" if macro else "static")
    pred_rows: list[dict[str, Any]] = []
    # Signal health: what each source resolved, per scenario that reached generation.
    attempted: list[str] = []
    geo_seen: dict[str, GeoResolution | None] = {}
    thr_seen: dict[str, ThroughputResolution | None] = {}
    macro_seen: dict[str, Any] = {}                 # macro-only path: first tier's macro provenance
    for name in names:
        if collector is not None:
            collector.set_scenario(name)                        # tag this scenario's calls
        try:
            resolved = catalog.spec(name)                       # replay drivers from the episode
        except ScenarioGenerationError as exc:                  # e.g. an uncovered replay window
            n_stopped += 1
            SCEN_SCENARIOS.labels(outcome="stopped").inc()
            report.stopped.append({"scenario": name, "reason": exc.reason, "detail": exc.detail})
            logger.error("  %-22s [all    ] STOPPED (fail-loud): %s", name, exc)
            continue
        version = store.register(name, resolved)                # governed, versioned definition
        if require_signoff and not signoff.is_approved(version):  # GATE: human approval
            n_held_signoff += len(tiers)
            SCEN_SCENARIOS.labels(outcome="held_signoff").inc(len(tiers))
            logger.warning("  %-22s [all    ] NOT SIGNED OFF v=%s -> held out", name, version)
            continue
        spec = scen_specs.get(name, {})
        attempted.append(name)
        geo_res = _geo_for(name, spec, geo_provider, valuation_date)
        thr_res = _throughput_for(name, spec, thr_provider, valuation_date)
        geo_seen[name], thr_seen[name] = geo_res, thr_res
        geo_mult = (geo_res.multiplier if geo_res is not None else 1.0) * \
                   (thr_res.multiplier if thr_res is not None else 1.0)
        try:
            sev = severity_spec(spec)
            opt_anchors, opt_source = None, None
            if sev is not None and sev["source"] == "options":    # #10: tiers from the option market
                opt_anchors, opt_source = option_anchors(sev["lead"], sev["direction"], market, sizing_cfg,
                                                         sev_cfg, plaus_cfg.get("bounds", {}))
                if opt_anchors is None:
                    option_flags.append({"scenario": name, "flag": "option_anchor_unavailable",
                                         "reason": opt_source.get("reason", "")})
                    logger.warning("  %-22s option anchors unavailable (%s) -> history anchors",
                                   name, opt_source.get("reason", ""))
            for tier in tiers:
                tier_gen, tier_checker = tools_for(tier, name)
                if sev is not None and anchor_book is not None:
                    ms = _materialize_anchored(name, tier, catalog, tier_gen, provider, prov_key=prov_key,
                                               state_raw=geo_mult, sev=sev, book=anchor_book, cfg=sev_cfg,
                                               bounds=replay_bounds if name in catalog.replays else None,
                                               anchors=opt_anchors, anchor_source=opt_source)
                    if sev["source"] == "options":
                        ms.provenance["concentration"] = stock_concentration(trades, etf_tickers)
                    if tier == "adverse":
                        flag = _option_tail_flag(name, sev, ms, market, sizing_cfg)
                        if flag is not None:
                            option_flags.append(flag)
                else:
                    ms = _materialize(name, tier, catalog, tier_gen, provider, prov_key=prov_key,
                                      severity_multiplier=geo_mult)
                catalog.stamp(name, ms)                         # replay window + crisis coverage
                if not dual_rag and "macro" in ms.provenance:
                    macro_seen.setdefault(name, ms.provenance["macro"])
                plaus = tier_checker.check(ms)
                if gate and not plaus.promotable:               # GATE: hold out of the run
                    n_held_gate += 1
                    SCEN_SCENARIOS.labels(outcome="held_gate").inc()
                    held = held_out_entry(name, tier, version, plaus)
                    report.held_out.append(held)
                    logger.warning("  %-22s [%-7s] GATE FAIL v=%s -> held out: %s",
                                   name, tier, version, held_out_summary(held))
                    continue
                res = stress_engine.run(
                    ms, trades, ctx, with_capital=(with_capital and not parallel_capital),
                    var_lookback=var_lookback, checker=tier_checker, base_result=base_capital)
                if liq_adjuster is not None:                     # F1: mid-market -> exit value
                    res.liquidity = liq_adjuster.adjust(res, liq_meta)
                if col_estimator is not None:                    # F2: cash / collateral line
                    res.collateral = col_estimator.estimate(res, liq_meta, ms.vector)
                res.anchoring = ms.provenance.get("severity")    # severity anchoring (None if unanchored)
                if res.anchoring is not None:                    # which Σ this tier materialized with
                    res.anchoring["covariance"] = ("production" if tier_gen.cov is catalog.covariance
                                                   else "crisis")
                if geo_res is not None:                          # F8: GPR severity + trigger
                    res.geopolitical = geo_res.to_dict()
                if thr_res is not None:                          # F9: AI-capex throughput severity
                    res.throughput = thr_res.to_dict()
                if dual_rag:                                     # value-add attribution + narrative
                    res.source = "dualrag"
                    attr = _attribution_for(name, ms, catalog, narrator, attribution_cache)
                    res.attribution = attr.to_dict()
                    res.narrative = attr.narrative
                report.results.append(res)
                horizon = int(ms.provenance.get("horizon_days", 10))
                pred_rows.append(prediction_record(
                    run_id=run_prov.run_id, scenario=name, severity=tier, layers=pred_layers,
                    as_of=valuation_date.isoformat(), horizon_days=horizon, vector=ms.vector,
                    scale=horizon_scale(catalog.covariance, horizon, ms.vector),
                    scenario_version=version, cov_id=catalog.covariance.cov_id,
                    snapshot_id=engine.snapshot_id,
                    composition_rule=(ANCHORED_RULE if dual_rag and sev is not None
                                      else f"{pred_rule}+anchored" if sev is not None else pred_rule),
                    triggers=live_triggers(
                        geopolitical=res.geopolitical, throughput=res.throughput,
                        news=getattr(provider, "news", None), crowding=getattr(provider, "crowding", None),
                        event_hits=_event_hits(provider, name)),
                    pit_clean=is_pit_clean(valuation_date.isoformat(), started), source="stress"))
                SCEN_SCENARIOS.labels(outcome="run").inc()
                if parallel_capital:                            # defer capital to the parallel pass
                    cap_jobs.append((name, tier, dict(ms.vector)))
                    cap_pending.append((name, tier, version, res))
                cap = res.capital
                cap_txt = (f" | capital Δ={cap.capital_delta:,.0f} "
                           f"(ES@{cap.level}: {cap.es_base:,.0f}->{cap.es_shocked:,.0f})"
                           if cap else "")
                exit_txt = (f" | exit P&L={res.liquidity['exit_pnl']:,.0f} "
                            f"(LH≤{res.liquidity['max_horizon_days']}d)"
                            if res.liquidity else "")
                cash_txt = (f" | cash out={res.collateral['net_cash_outflow']:,.0f}"
                            if res.collateral else "")
                logger.info("  %-22s [%-7s] v=%s %-4s P&L=%15s%s%s%s",
                            name, tier, version, plaus.status, f"{res.total_pnl:,.0f}",
                            cap_txt, exit_txt, cash_txt)
        except ScenarioGenerationError as exc:                  # fail-loud, per scenario
            n_stopped += 1
            SCEN_SCENARIOS.labels(outcome="stopped").inc()
            report.stopped.append({"scenario": name, "reason": exc.reason, "detail": exc.detail})
            logger.error("  %-22s [all    ] STOPPED (fail-loud): %s", name, exc)
            continue

    if parallel_capital and cap_jobs:                      # Fix 2: shocked ES fanned over processes
        logger.info("Capital: %d shocked combo(s) over %d worker(s) (base ES computed once)",
                    len(cap_jobs), n_workers)
        shocked = shocked_es_parallel(config, root, ctx, var_lookback, book, asset_class,
                                      cap_jobs, n_workers)
        key = f"{DEFAULT_CAPITAL_LEVEL}"
        es_base = float(base_capital.es.get(key, 0.0)) if base_capital is not None else 0.0
        base_lb = base_capital.lookback_days if base_capital is not None else 0
        for c_name, c_tier, _c_ver, c_res in cap_pending:
            es_map, lb = shocked.get((c_name, c_tier), ({}, base_lb))
            c_res.capital = ScenarioCapital(
                level=DEFAULT_CAPITAL_LEVEL, es_base=es_base,
                es_shocked=float(es_map.get(key, 0.0)), lookback_days=lb)
            logger.info("  %-22s [%-7s] capital Δ=%s (ES@%s: %s->%s)",
                        c_name, c_tier, f"{c_res.capital.capital_delta:,.0f}",
                        DEFAULT_CAPITAL_LEVEL, f"{c_res.capital.es_base:,.0f}",
                        f"{c_res.capital.es_shocked:,.0f}")

    if collector is not None:                               # P3: persist the captured call-trace
        unbind_collector(trace_token)
        report.trace = collector.summary()
        trace_out = write_trace(collector, valuation_date.isoformat(), root)
        logger.info("Wrote call-trace (%d call(s), $%.4f) -> %s", len(collector.records),
                    collector.summary()["total_cost_usd"], trace_out)

    report.signal_health = _signal_health(
        root, valuation_date, attempted=attempted, scen_specs=scen_specs, provider=provider,
        macro=macro, macro_seen=macro_seen, stopped={s["scenario"]: str(s["reason"]) for s in report.stopped},
        geo=geo_seen, thr=thr_seen, market=market, trades=trades, events_cfg=events_cfg)
    report.tier_flags = tier_order_flags(report.results) + option_flags
    for flag in report.tier_flags:                    # crisis Σ on a convex book can do this; surfaced
        if flag["flag"] != "severe_below_adverse":
            continue
        logger.warning("  %-22s severe P&L %s above adverse %s (severe tier through crisis Σ)",
                       flag["scenario"], f"{flag['severe_pnl']:,.0f}", f"{flag['adverse_pnl']:,.0f}")
    out = write_stress_report(report, root)
    PredictionLog(root).write(pred_rows, run_prov.run_id)
    logger.info("Wrote stress report -> %s", out)
    n_breaches = _emit_dashboard(report, run_prov.run_id, root)

    duration = time.perf_counter() - t0
    SCEN_DURATION.observe(duration)
    SCEN_RUNS.labels(status="ok").inc()
    manifest = RunManifest(
        module="scenariogen", run_id=run_prov.run_id,
        started_at=started.isoformat(), finished_at=datetime.now(UTC).isoformat(),
        duration_s=duration, status="ok", partition=valuation_date.isoformat(),
        git_sha=run_prov.git_sha, config_hash=run_prov.config_hash,
        counts={"n_trades": len(trades), "n_run": len(report.results),
                "n_held_gate": n_held_gate, "n_held_signoff": n_held_signoff,
                "n_stopped": n_stopped, "n_breaches": n_breaches,
                "n_trace_calls": len(collector.records) if collector is not None else 0},
        extra={"cov_id": catalog.covariance.cov_id, "used_macro": str(macro),
               "used_dualrag": str(dual_rag),
               "narrative_cost_usd": (f"{narrator.tracker.total_cost_usd:.6f}"
                                      if narrator is not None else "0"),
               "snapshot_id": engine.snapshot_id,
               "factor_snapshot_id": factor_snap.snapshot_id},
    )
    RunManifestStore(root, "scenariogen").write(manifest)
    return report


def _emit_dashboard(report: StressReport, run_id: str, root: Path) -> int:
    """Persist the queryable scenario-result rows and log the summary + limit breaches.

    Returns the number of limit breaches detected.
    """
    if not report.results:
        return 0
    ScenarioResultStore(root).write_run(stress_rows(report.results, run_id), run_id)
    dash = StressDashboard(report.results, run_id)
    logger.info("Scenario dashboard:\n%s", dash.to_markdown())
    limits = load_limits_config(root)
    breaches = dash.breaches(loss_limit=limits.get("loss_limit"),
                             capital_limit=limits.get("capital_limit"))
    for _, b in breaches.iterrows():
        SCEN_LIMIT_BREACH.inc()
        logger.warning("  LIMIT BREACH %-22s [%-7s] P&L=%s capital Δ=%s",
                       b["scenario"], b["severity"], f"{b['total_pnl']:,.0f}",
                       f"{b['capital_delta']:,.0f}")
    return len(breaches)


def _signal_health(root: Path, as_of: date, *, attempted: list[str], scen_specs: dict[str, Any],
                   provider: MacroScenarioProvider | DualRagEnrichmentProvider | None, macro: bool,
                   macro_seen: dict[str, Any], stopped: dict[str, str],
                   geo: dict[str, GeoResolution | None], thr: dict[str, ThroughputResolution | None],
                   market: OptionSignalProvider | None, trades: list[Any],
                   events_cfg: EventsConfig | None) -> dict[str, Any]:
    """Collect each source's observation from what the run resolved and roll it up (signal-health).

    Logs one line per source that is not ok or unused and sets the ``scenariogen_signal_status`` gauge.

    Args:
        root: Project root (for ``signal_health.json`` and, on the macro-only path, ``macro.json``).
        as_of: The valuation date.
        attempted: Scenarios that reached generation, in run order.
        scen_specs: The resolved scenario specs (``geopolitical`` / ``throughput`` tags).
        provider: The run's driver provider (None on the static path or when MacroRAG was down).
        macro: Whether this is the macro-only path.
        macro_seen: Macro-only path: scenario -> its macro resolution dict.
        stopped: Scenario -> the reason the run stopped it.
        geo: Scenario -> its GPR resolution (None without a feed).
        thr: Scenario -> its throughput resolution (None without a feed).
        market: The option-signal provider (None without option data at or before as_of).
        trades: The book.
        events_cfg: ``events.json`` (crowding reliability floors), or None.

    Returns:
        The report's ``signal_health`` block.
    """
    obs: dict[str, Observation] = {}
    if isinstance(provider, DualRagEnrichmentProvider):
        macro_res, arag_res = provider.macro_resolutions, provider.arag_resolutions
        obs["macrorag"] = macro_observation([n for n in attempted if provider.requires_macro(n)],
                                            macro_res, stopped)
        # XbrlRag was reached by every scenario it resolved and every one it stopped; a scenario stopped
        # for missing macro legs or an unbuildable crisis Σ never got to it.
        arag_cfg: dict[str, Any] = getattr(provider.arag, "scen_cfg", None) or {}
        reached = [n for n in attempted if n in arag_res
                   or (n in stopped and stopped[n] not in _MACRO_STOPS and stopped[n] != CRISIS_UNAVAILABLE)]
        obs["xbrlrag"] = xbrlrag_observation(
            {n: len((arag_cfg.get(n) or {}).get("companies", [])) for n in reached} if provider.arag else {},
            arag_res, stopped)
        obs["news"] = news_observation(provider.news)
        obs["crowding"] = crowding_observation(
            provider.crowding, as_of,
            min_obs=events_cfg.crowding_min_obs if events_cfg is not None else None,
            max_staleness_days=events_cfg.crowding_max_staleness_days if events_cfg is not None else None)
    elif macro:
        configured = set(load_macro_config(root).get("scenarios", {}))
        expected = [n for n in attempted if n in configured]
        down = {} if provider is not None else dict.fromkeys(expected, FALLBACK_UNAVAILABLE)
        obs["macrorag"] = macro_observation(expected, macro_seen, {**down, **stopped})
    underliers = {t.underlier for t in trades if isinstance(t, EquityOptionTrade)}
    obs["options"] = options_observation(market, underliers)
    obs["gpr"] = state_observation(
        [n for n in attempted if isinstance(scen_specs.get(n, {}).get("geopolitical"), dict)], geo,
        date_attr="asof_date")
    obs["throughput"] = state_observation(
        [n for n in attempted if isinstance(scen_specs.get(n, {}).get("throughput"), dict)], thr)

    block = build_signal_health(as_of, SignalHealthConfig.load(root), obs)
    for s in block["sources"]:
        SCEN_SIGNAL.labels(source=s["source"]).set(STATUS_GAUGE[s["status"]])
    logger.info("Signal health: %s (%s)", block["overall"],
                ", ".join(f"{k} {v}" for k, v in block["counts"].items() if v))
    for line in problem_lines(block):
        logger.warning("  signal %s", line)
    return block


def _geo_for(name: str, spec: dict[str, Any], provider: GeopoliticalProvider | None,
             as_of: date) -> GeoResolution | None:
    """Resolve the F8 geopolitical calibration for a scenario tagged ``geopolitical`` in config.

    Untagged scenario, or no provider/feed -> None (multiplier 1.0, no provenance). A tagged
    scenario names its GPR series (default ``GPRC_TWN``); the resolution carries the severity
    multiplier + live-trigger, PIT to ``as_of``.
    """
    tag = spec.get("geopolitical")
    if provider is None or not isinstance(tag, dict):
        return None
    res = provider.resolve(tag.get("series"), as_of)
    if res.applied:
        logger.info("  %-22s geopolitical: %s=%.2f pct=%.0f%% z=%.2f -> x%.3f%s",
                    name, res.series, res.level, res.percentile * 100, res.z, res.multiplier,
                    " TRIGGER" if res.trigger else "")
    return res


def _throughput_for(name: str, spec: dict[str, Any], provider: ThroughputProvider | None,
                    as_of: date) -> ThroughputResolution | None:
    """Resolve the F9 throughput calibration for a scenario tagged ``throughput`` in config.

    Untagged scenario, or no provider/feed -> None (multiplier 1.0). A tagged scenario (ai_bust) has
    its whole shock sized by the AI-capex throughput: decelerating throughput amplifies the bust.
    """
    if provider is None or not isinstance(spec.get("throughput"), dict):
        return None
    res = provider.resolve(as_of)
    if res.applied:
        logger.info("  %-22s throughput: IPG YoY=%.1f%% pct=%.0f%% thesis=%s -> x%.3f%s",
                    name, (res.ipg_yoy or 0) * 100, (res.ipg_percentile or 0) * 100, res.thesis,
                    res.multiplier, " TRIGGER" if res.trigger else "")
    return res


def _materialize(name: str, tier: str, catalog: ScenarioCatalog, generator: ScenarioGenerator,
                 provider: MacroScenarioProvider | DualRagEnrichmentProvider | None,
                 *, prov_key: str = "macro",
                 severity_multiplier: float = 1.0) -> MaterializedScenario:
    """Materialize a scenario, sourcing drivers/overlays from the wired provider.

    ``provider`` is either the MacroRAG-only ``MacroScenarioProvider`` (``prov_key='macro'``) or
    the ``DualRagEnrichmentProvider`` (``prov_key='dualrag'``); both expose ``definition_for``
    and a resolution with ``to_dict()`` that is pinned into the materialized provenance. The
    dual provider may raise ``ScenarioGenerationError`` (fail-loud), handled by the caller.
    """
    if provider is None:
        return catalog.materialize(name, tier, severity_multiplier=severity_multiplier)
    defn, resolution = provider.definition_for(
        name, severity=tier, severity_scale=catalog.severity_tiers[tier] * severity_multiplier)
    ms = generator.materialize(defn)
    ms.provenance[prov_key] = resolution.to_dict()      # pin which legs came from which service
    return ms


def _event_hits(provider: Any, name: str) -> list[Any] | None:
    """The scenario's 8-K trigger hits when the provider carries ``events.json`` themes, else None."""
    events = getattr(provider, "events", None)
    return events.triggers(name) if events is not None else None


def stock_concentration(trades: list[Any], excluded: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The book's single-name cash-equity notional per ticker (signed by side), largest long first.

    Shown on an option-anchored largest-name event so a change of the largest name is visible.
    ``excluded`` (the ETFs of ``events.json``) are left out.
    """
    by: dict[str, float] = {}
    for t in trades:
        if isinstance(t, CashEquityTrade) and t.ticker.upper() not in excluded:
            by[t.ticker] = by.get(t.ticker, 0.0) + math.copysign(float(t.notional), float(t.quantity))
    return [{"ticker": k, "notional": v} for k, v in sorted(by.items(), key=lambda kv: -kv[1])]


def _option_tail_flag(name: str, sev: dict[str, str], ms: MaterializedScenario, market: Any,
                      sizing: OptionSizingConfig) -> dict[str, Any] | None:
    """``below_option_tail`` when the option market prices a more extreme lead move than the adverse tier."""
    lead = sev["lead"]
    horizon = int(ms.provenance.get("horizon_days", 10))
    if market is None or not lead.startswith("eq:") or horizon > sizing.check_max_horizon_days:
        return None
    q = sizing.check_q if sev["direction"] == "down" else 1.0 - sizing.check_q
    got = market.quantile(lead.split(":", 1)[1], q)
    move = float(ms.vector.get(lead, 0.0))
    if got is None or not exceeds_option_tail(move, got[0]):
        return None
    return {"scenario": name, "flag": "below_option_tail", "tier": "adverse", "lead": lead,
            "lead_move": move, "option_move": got[0], "source": got[1]}


def _materialize_anchored(name: str, tier: str, catalog: ScenarioCatalog, generator: ScenarioGenerator,
                          provider: MacroScenarioProvider | DualRagEnrichmentProvider | None, *,
                          prov_key: str, state_raw: float, sev: dict[str, str], book: AnchorBook,
                          cfg: SeverityConfig,
                          bounds: dict[str, float] | None = None,
                          anchors: dict[str, float] | None = None,
                          anchor_source: dict[str, Any] | None = None) -> MaterializedScenario:
    """Materialize an anchored tier: compose at tier scale 1, then rescale onto the history anchor.

    The state multiplier is floored at ``cfg.state_floor``. On the dual-RAG path the company legs are
    reseeded with their company-specific part and their stacked multipliers capped (``leg_transform``).
    The authored-only definition (no enrichment, no state) is the reference for the enrichment's
    relative effect on the lead factor. ``bounds`` (replays) caps the anchored scale at the hard
    plausibility bounds. ``anchors``/``anchor_source`` replace the history anchors (an option-anchored
    scenario) or record why it fell back to them.
    """
    state_used = max(state_raw, cfg.state_floor)
    authored = replace(catalog.definition(name, tier), severity_scale=1.0)
    record: dict[str, Any] = {}
    resolution: Any = None
    if isinstance(provider, DualRagEnrichmentProvider):
        composed, resolution = provider.definition_for(
            name, severity=tier, severity_scale=1.0, state_mult=state_used,
            leg_transform=leg_transform(generator, cfg.leg_cap, record, lead=sev["lead"]))
    elif provider is not None:
        composed, resolution = provider.definition_for(name, severity=tier, severity_scale=state_used)
    else:
        composed = replace(authored, severity_scale=state_used)
    ms = anchor_tier(name, tier, lead=sev["lead"], direction=sev["direction"], composed=composed,
                     authored=authored, generator=generator, book=book, cfg=cfg,
                     state_raw=state_raw, state_used=state_used, legs=record, bounds=bounds,
                     anchors=anchors, anchor_source=anchor_source)
    if resolution is not None:
        ms.provenance[prov_key] = resolution.to_dict()
    return ms


def _attribution_for(name: str, ms: MaterializedScenario, catalog: ScenarioCatalog,
                     narrator: AttributionNarrator | None,
                     cache: dict[str, ScenarioAttribution]) -> ScenarioAttribution:
    """Build (once per scenario, cached) the value-add attribution + optional narrative."""
    if name in cache:
        return cache[name]
    attr = build_attribution(ms.provenance.get("dualrag", {}), catalog.spec(name))
    if narrator is not None:
        attr.narrative = narrator.generate(attr)
    cache[name] = attr
    logger.info("  %-22s attribution: macro=%s arag=%s (narrative=%s)",
                name, attr.macrorag.status, attr.arag.status, "yes" if attr.narrative else "no")
    return attr


def run_reverse_stress(
    asset_class: str | None = None,
    book: str | None = None,
    valuation_date: date | None = None,
    scenario: str | None = None,
    target_loss: float | None = None,
    cov_lookback: int | None = None,
    macro: bool = False,
) -> list[ReverseStressResult]:
    """Reverse stress (design §5D): solve the severity λ on each scenario's axis that
    drives the full-revaluation loss to the loss limit — "what would break us?"."""
    root = get_project_root()
    config = load_pricer_config()
    pf_cfg = load_portfolio_config(book=book)
    valuation_date = valuation_date or pf_cfg.default_valuation_date
    ctx = _build_ctx(config, valuation_date, "PV")
    run_prov = RunProvenance.capture(root, config)

    engine = PricingEngine(config, root)
    catalog = ScenarioCatalog.from_config(FactorHistory(root, config), lookback=cov_lookback,
                                          project_root=root)
    generator = ScenarioGenerator(catalog.covariance)
    provider = (_build_macro_provider(root, valuation_date, catalog.scenario_config())
                if macro else None)
    limits = load_limits_config(root)
    rs = limits.get("reverse_stress", {})
    solver = ReverseStressSolver(engine, generator, max_scale=float(rs.get("max_scale", 10.0)),
                                 tol_rel=float(rs.get("tol_rel", 0.01)),
                                 max_iter=int(rs.get("max_iter", 40)))
    target = target_loss if target_loss is not None else float(limits["loss_limit"])
    trades = load_portfolio(asset_class=asset_class, book=book)
    logger.info("Reverse stress | valuation_date=%s | %d trade(s) | target loss=%s",
                valuation_date, len(trades), f"{target:,.0f}")

    results: list[ReverseStressResult] = []
    for name in ([scenario] if scenario else catalog.names()):
        defn, _ = (provider.definition_for(name) if provider
                   else (catalog.definition(name, "adverse"), None))
        r = solver.solve(defn, trades, ctx, target)
        results.append(r)
        verdict = (f"breaches at λ={r.severity_scale:.2f}x (loss {r.achieved_loss:,.0f})"
                   if r.reachable else
                   f"NOT reachable within {solver.max_scale:.0f}x (max loss {r.achieved_loss:,.0f})")
        logger.info("  %-22s target %s -> %s", name, f"{target:,.0f}", verdict)

    report = ReverseStressReport(
        valuation_date=valuation_date.isoformat(), run_id=run_prov.run_id,
        snapshot_id=engine.snapshot_id, target_loss=target,
        results=[r.to_dict() for r in results])
    out = write_reverse_stress_report(report, root)
    logger.info("Wrote reverse-stress report (%d scenario(s)) -> %s", len(results), out)
    return results


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="scenariogen stress-test runner")
    p.add_argument("--asset-class", default=None)
    p.add_argument("--book", default=None,
                   help="portfolio book variant (base|balanced|core|aggressive|risky); default: active default")
    p.add_argument("--workers", type=int, default=None,
                   help="parallel processes for ES-under-shock capital (per scenario x tier); default: sequential")
    p.add_argument("--valuation-date", default=None, help="ISO date; default: portfolio default")
    p.add_argument("--scenario", default=None, help="one scenario id; default: all")
    p.add_argument("--severity", default=None, help="one tier (mild|adverse|severe); default: all")
    p.add_argument("--no-capital", action="store_true",
                   help="skip ES-under-shock capital (heavy: full hist-VaR per scenario)")
    p.add_argument("--var-lookback", type=int, default=None, help="hist-VaR lookback days")
    p.add_argument("--cov-lookback", type=int, default=None, help="covariance lookback days")
    p.add_argument("--no-gate", action="store_true",
                   help="run even non-promotable (plausibility-FAIL) scenarios")
    p.add_argument("--macro", action="store_true",
                   help="source macro-leg drivers from MacroRAG (else static config drivers)")
    p.add_argument("--dual-rag", action="store_true",
                   help="dual-RAG: macro legs from MacroRAG + company legs from ARag "
                        "(fail-loud preflight; supersedes --macro)")
    p.add_argument("--no-narrative", action="store_true",
                   help="skip the LLM value-add narrative (deterministic attribution still written)")
    p.add_argument("--require-signoff", action="store_true",
                   help="only run scenario versions an analyst has signed off (S5)")
    p.add_argument("--sensitivities", action="store_true",
                   help="also run the sensitivity library (scenarios.json 'sensitivities')")
    p.add_argument("--no-exit-value", action="store_true",
                   help="skip the P4 liquidity-horizon exit value (F1) + cash/collateral line (F2)")
    p.add_argument("--reverse-stress", action="store_true",
                   help="solve the severity that drives loss to the limit (design §5D)")
    p.add_argument("--target-loss", type=float, default=None,
                   help="reverse-stress target loss in USD (default: limits.json loss_limit)")
    return p.parse_args()


if __name__ == "__main__":
    _setup_logging()
    args = _parse_args()
    vdate = date.fromisoformat(args.valuation_date) if args.valuation_date else None
    if args.reverse_stress:
        run_reverse_stress(
            asset_class=args.asset_class,
            book=args.book,
            valuation_date=vdate,
            scenario=args.scenario,
            target_loss=args.target_loss,
            cov_lookback=args.cov_lookback,
            macro=args.macro,
        )
    else:
        run_stress(
            asset_class=args.asset_class,
            book=args.book,
            valuation_date=vdate,
            scenario=args.scenario,
            severity=args.severity,
            with_capital=not args.no_capital,
            var_lookback=args.var_lookback,
            cov_lookback=args.cov_lookback,
            gate=not args.no_gate,
            macro=args.macro,
            dual_rag=args.dual_rag,
            narrative=not args.no_narrative,
            require_signoff=args.require_signoff,
            exit_value=not args.no_exit_value,
            workers=args.workers,
            sensitivities=args.sensitivities,
        )
