"""BFF FastAPI app (Phase 0).

Endpoints (all under ``/api``):
  GET /api/health               BFF liveness + version
  GET /api/system/runs          cross-module run history (DuckDB union of data/*/store/runs/)
  GET /api/system/health        proxy of the upstream pricer service /health (graceful)
  GET /api/system/metrics       proxy of the upstream /metrics (Prometheus text)
  GET /api/governance/posture   control-room rollup derived from stored runs

Run it (from the repo root):
  conda run --no-capture-output -n py312 uvicorn ui.bff.app.main:app --port 8088 --reload
"""

from __future__ import annotations

import json

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import (
    __version__,
    audit,
    governance,
    jobs,
    llmops,
    marketdata,
    news,
    options_signals,
    pricing,
    risk,
    scenarios,
    sizing,
    system,
)
from .security import require_write, roles_config, write_auth_mode
from .settings import cors_origins, service_url
from .stores import governance_posture, recent_runs

app = FastAPI(
    title="Faultline BFF",
    version=__version__,
    summary="Read layer the React UI consumes (Phase 0 foundations).",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    """BFF liveness — does not depend on the upstream service."""
    return {"status": "ok", "service": "qlp-bff", "version": __version__}


@app.get("/api/system/runs", tags=["system"])
def system_runs(
    limit: int = Query(50, ge=1, le=500),
    module: str | None = Query(None, description="filter: pricer | risk | scenariogen | marketdata"),
) -> dict[str, object]:
    """Cross-module run history, newest first."""
    runs = recent_runs(limit=limit, module=module)
    modules = sorted({r["module"] for r in runs})
    return {"runs": runs, "modules": modules, "count": len(runs)}


@app.get("/api/system/runs/{run_id}", tags=["system"])
def system_run_trace(run_id: str) -> dict[str, object]:
    """Run trace (S3, manifest-level): stages, outputs, and the reproduce recipe."""
    return system.run_trace(run_id)


@app.get("/api/system/pipeline", tags=["system"])
def system_pipeline(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Pipeline DAG (S4): runs grouped by the market-data snapshot they consumed."""
    return system.pipeline(valuation_date)


@app.get("/api/system/modules", tags=["system"])
def system_modules() -> dict[str, object]:
    """Module / architecture map (S5)."""
    return system.modules()


@app.get("/api/system/config", tags=["system"])
def system_config() -> dict[str, object]:
    """Config explorer (S6): model config + operational settings (secrets masked)."""
    return system.config()


@app.get("/api/system/provenance", tags=["system"])
def system_provenance(snapshot_id: str | None = Query(None), run_id: str | None = Query(None),
                      portfolio_version: str | None = Query(None)) -> dict[str, object]:
    """Provenance pivot (L3): which runs share a snapshot / run / portfolio version."""
    return system.provenance(snapshot_id, run_id, portfolio_version)


@app.get("/api/system/health", tags=["system"])
def system_health() -> dict[str, object]:
    """Proxy the upstream pricer service /health; degrade gracefully when it's down."""
    url = f"{service_url()}/health"
    try:
        resp = httpx.get(url, timeout=2.0)
        resp.raise_for_status()
        return {"upstream": "up", "url": url, "data": resp.json()}
    except Exception as exc:  # service not running is the normal local case
        return {"upstream": "down", "url": url, "error": str(exc)}


@app.get("/api/system/metrics", tags=["system"])
def system_metrics() -> Response:
    """Proxy the upstream Prometheus /metrics (text exposition)."""
    url = f"{service_url()}/metrics"
    try:
        resp = httpx.get(url, timeout=2.0)
        resp.raise_for_status()
        return Response(content=resp.text, media_type="text/plain; charset=utf-8")
    except Exception as exc:
        return Response(content=f"# upstream metrics unavailable: {exc}\n",
                        media_type="text/plain; charset=utf-8", status_code=503)


@app.get("/api/governance/posture", tags=["governance"])
def posture(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Control-room posture derived from stored run manifests + risk JSON (no engine calls)."""
    return governance_posture(valuation_date)


# --- pricing (Phase 1, stored-only) --------------------------------------- #


@app.get("/api/pricing/dates", tags=["pricing"])
def pricing_dates() -> dict[str, object]:
    """Valuation dates available in the results store, newest first."""
    dates = pricing.available_dates()
    return {"dates": dates, "default": dates[0] if dates else None}


@app.get("/api/pricing/book", tags=["pricing"])
def pricing_book(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Book valuation (PR-B1): totals, by asset class, and the per-trade list with deltas."""
    return pricing.book(valuation_date)


@app.get("/api/pricing/book/history", tags=["pricing"])
def pricing_book_history() -> dict[str, object]:
    """Book & per-asset-class PV time-series across dates, with period-over-period P&L."""
    return pricing.book_history()


@app.get("/api/pricing/trade/{trade_id}", tags=["pricing"])
def pricing_trade(trade_id: str, valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Trade valuation (PR-B2): the full stored per-trade result."""
    result = pricing.trade_detail(trade_id, valuation_date)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no stored result for {trade_id}")
    return result


@app.get("/api/pricing/trade/{trade_id}/docs", tags=["pricing"])
def pricing_trade_docs(trade_id: str) -> dict[str, object]:
    """Pricing methodology write-up for a trade (docs/pricing/*): per-trade page + instrument
    methodology + foundations, as ordered sections for the UI's "How it's priced" panel."""
    result = pricing.trade_docs(trade_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown trade {trade_id}")
    return result


@app.get("/api/pricing/pv-history/{trade_id}", tags=["pricing"])
def pricing_pv_history(trade_id: str) -> dict[str, object]:
    """PV history (PR-B4): latest PV per valuation date for one trade."""
    return pricing.pv_history(trade_id)


@app.get("/api/pricing/live/{trade_id}", tags=["pricing"])
def pricing_live(trade_id: str, valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Live re-price against the upstream pricer service snapshot (graceful when it's down)."""
    return pricing.live_price(trade_id, valuation_date)


@app.get("/api/lineage/{trade_id}", tags=["pricing"])
def lineage(trade_id: str, valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Calculation lineage (PR-B3): inputs → intermediates → final value, from stored explain."""
    return pricing.lineage(trade_id, valuation_date)


@app.get("/api/governance/model-inventory", tags=["governance"])
def model_inventory() -> dict[str, object]:
    """Governed model registry (PR-B5), from config, with portfolio coverage."""
    return pricing.model_inventory()


@app.get("/api/governance/report", tags=["governance"])
def governance_report() -> dict[str, object]:
    """Governance report (G3/G4): golden regression + IPV + inventory coverage (stored)."""
    return governance.report()


@app.get("/api/governance/drift", tags=["governance"])
def governance_drift() -> dict[str, object]:
    """PV-drift posture (G6) from the pricer run manifests."""
    return governance.drift()


# --- risk (Phase 2, stored-only) ------------------------------------------ #


@app.get("/api/risk/dates", tags=["risk"])
def risk_dates() -> dict[str, object]:
    """Valuation dates with a stored risk report, newest first."""
    dates = risk.available_dates()
    return {"dates": dates, "default": dates[0] if dates else None}


@app.get("/api/risk/report", tags=["risk"])
def risk_report(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Full risk report (RK-B1/B2/B4/B5/B6) from the latest stored run for the date."""
    return risk.report(valuation_date)


@app.get("/api/risk/var-construction", tags=["risk"])
def risk_var_construction(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """VaR construction (RK-B3b): the historical daily P&L vector + VaR/ES cut-lines."""
    return risk.var_construction(valuation_date)


@app.get("/api/risk/limits", tags=["risk"])
def risk_limits(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Limit utilisation (RK-B6): every configured limit with observed value + headroom."""
    return risk.limits(valuation_date)


class LimitEditRequest(BaseModel):
    kind: str  # var | sensitivity
    key: str   # VaR confidence level, or sensitivity factor name
    ceiling: float
    note: str = ""


@app.post("/api/risk/limits", tags=["risk"])
def risk_limit_edit(
    body: LimitEditRequest, actor: str = Depends(require_write("risk.limit.edit")),
) -> dict[str, object]:
    """Gated write: edit an existing risk-limit ceiling in the pricer config. Audited."""
    result = risk.update_limit(body.kind, body.key, body.ceiling)
    audit.record_action(
        "risk.limit.edit", f"{body.kind}:{body.key}", actor,
        reason=body.note, outcome="ok" if result.get("ok") else "error",
        details={"kind": body.kind, "key": body.key, "ceiling": body.ceiling,
                 "old_ceiling": result.get("old_ceiling"), "error": result.get("error")},
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# --- market data (Phase 3, read layer over files) ------------------------- #


@app.get("/api/md/feeds", tags=["marketdata"])
def md_feeds() -> dict[str, object]:
    """Feed catalog (MD-B1): one row per CSV, with source, freshness, DQ, consumers."""
    return marketdata.list_feeds()


@app.get("/api/md/overview", tags=["marketdata"])
def md_overview() -> dict[str, object]:
    """Market-data landing rollup: freshness + DQ + consumption summarised across all feeds."""
    return marketdata.overview()


@app.get("/api/md/dependency", tags=["marketdata"])
def md_dependency() -> dict[str, object]:
    """Feed→trade dependency matrix: which trades read each consumed feed, and the reverse."""
    return marketdata.dependency()


@app.get("/api/md/feeds/{feed_id:path}/series", tags=["marketdata"])
def md_feed_series(feed_id: str, limit: int = Query(500, ge=10, le=5000)) -> dict[str, object]:
    """Feed time series (MD-B3): down-sampled rows for the data viewer."""
    result = marketdata.feed_series(feed_id, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no feed {feed_id}")
    return result


@app.get("/api/md/feeds/{feed_id:path}/rows", tags=["marketdata"])
def md_feed_rows(
    feed_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=5, le=200),
    filters: str | None = Query(None, description="JSON object of column->substring"),
    sort_col: str | None = Query(None),
    sort_dir: str = Query("asc"),
) -> dict[str, object]:
    """Raw feed rows (MD-B6): per-column filter + sort + pagination for the data grid."""
    parsed: dict[str, str] = {}
    if filters:
        try:
            loaded = json.loads(filters)
            if isinstance(loaded, dict):
                parsed = {str(k): str(v) for k, v in loaded.items()}
        except (ValueError, TypeError):
            parsed = {}
    result = marketdata.feed_rows(
        feed_id, page=page, page_size=page_size, filters=parsed,
        sort_col=sort_col, sort_dir=sort_dir,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"no feed {feed_id}")
    return result


@app.get("/api/md/feeds/{feed_id:path}", tags=["marketdata"])
def md_feed_detail(feed_id: str) -> dict[str, object]:
    """Feed detail (MD-B2): source config, provenance, DQ checks, consuming trades."""
    result = marketdata.feed_detail(feed_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no feed {feed_id}")
    return result


@app.get("/api/md/coverage", tags=["marketdata"])
def md_coverage() -> dict[str, object]:
    """Feed↔trade coverage (MD-B4): asset-class-level linkage."""
    return marketdata.coverage()


@app.get("/api/news/events", tags=["news"])
def news_events(
    ticker: str | None = Query(None, description="filter to one underlier"),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, object]:
    """P6/A5: the SEC 8-K item-code event stream (newest first, item codes decoded)."""
    return news.list_events(ticker, limit)


@app.get("/api/news/calibration", tags=["news"])
def news_calibration(as_of: str | None = Query(None, description="ISO date; default today (PIT)")) -> dict[str, object]:
    """The resolved news calibration: gate, per-name multiplier, 5.02 key-man triggers, earnings."""
    return news.calibration(as_of)


@app.get("/api/news/{ticker}", tags=["news"])
def news_name(ticker: str, as_of: str | None = Query(None)) -> dict[str, object]:
    """Per-name news: 8-K events + GDELT salience series + the resolved multiplier leg."""
    result = news.name_detail(ticker, as_of)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no news events for {ticker!r}")
    return result


@app.get("/api/options/signals", tags=["options-signals"])
def options_signals_list() -> dict[str, object]:
    """P6/B6: name catalog of option-implied signals (latest per underlier + availability flags)."""
    return options_signals.list_signals()


@app.get("/api/options/signals/credit", tags=["options-signals"])
def options_signals_credit() -> dict[str, object]:
    """Option-implied credit across names, ranked by distance-to-default (B3)."""
    return options_signals.credit_table()


@app.get("/api/options/signals/{name}", tags=["options-signals"])
def options_signals_detail(name: str) -> dict[str, object]:
    """Full option-implied signal detail for one underlier + short history + implied credit."""
    result = options_signals.signal_detail(name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no option signals for {name!r}")
    return result


@app.get("/api/options/signals/{name}/rnd", tags=["options-signals"])
def options_signals_rnd(name: str) -> dict[str, object]:
    """Latest risk-neutral density (strike grid + pdf + cdf + tail probs) for one underlier."""
    result = options_signals.rnd_curve(name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no RND curve for {name!r}")
    return result


@app.get("/api/md/dq", tags=["marketdata"])
def md_dq() -> dict[str, object]:
    """Data-quality dashboard (MD-B5): gate verdicts over the declared files."""
    return marketdata.dq_dashboard()


# --- stress scenarios (Phase 4, stored-only) ------------------------------ #


@app.get("/api/scenarios", tags=["scenarios"])
def scenarios_catalog(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Scenario catalog (SC-B1): scenario x tier grid with status, P&L, capital, sign-off."""
    return scenarios.catalog(valuation_date)


@app.get("/api/scenarios/covariance", tags=["scenarios"])
def scenarios_covariance() -> dict[str, object]:
    """Correlation matrix (SC-B2 heatmap) — Σ.correlation() for the shock-derivation view."""
    return scenarios.correlation()


@app.get("/api/scenarios/versions", tags=["scenarios"])
def scenarios_versions() -> dict[str, object]:
    """Scenario version history (SC-B7) with sign-off status."""
    return scenarios.versions()


@app.get("/api/scenarios/signoff", tags=["scenarios"])
def scenarios_signoff() -> dict[str, object]:
    """Sign-off records (SC-B5), latest per version."""
    return scenarios.signoff()


class SignoffRequest(BaseModel):
    scenario_version: str
    scenario: str = ""
    status: str  # approved | rejected
    note: str = ""


@app.post("/api/scenarios/signoff", tags=["scenarios"])
def scenarios_signoff_write(
    body: SignoffRequest, actor: str = Depends(require_write("scenario.signoff")),
) -> dict[str, object]:
    """Gated write: approve/reject a scenario version. Records to the sign-off store + audit."""
    result = scenarios.record_signoff(body.scenario_version, body.scenario, body.status, actor, body.note)
    audit.record_action(
        "scenario.signoff", body.scenario_version, actor,
        reason=body.note, outcome="ok" if result.get("ok") else "error",
        details={"scenario": body.scenario, "status": body.status.strip().lower(),
                 "error": result.get("error")},
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/write/mode", tags=["system"])
def write_mode() -> dict[str, object]:
    """Whether the BFF requires a write token (token) or runs local-trust."""
    return {"mode": write_auth_mode()}


@app.get("/api/roles", tags=["system"])
def roles() -> dict[str, object]:
    """RBAC config: operator→role map, default role, and per-action required role."""
    return roles_config()


@app.get("/api/audit", tags=["system"])
def audit_log(limit: int = Query(100, ge=1, le=1000)) -> dict[str, object]:
    """Append-only audit trail of BFF writes, newest first."""
    return {"entries": audit.recent(limit)}


# --- job runner (gated 'run' writes: triggering + reverse-stress solve) ---- #


@app.get("/api/jobs/catalog", tags=["system"])
def jobs_catalog() -> dict[str, object]:
    """The whitelisted runs that can be triggered from the UI."""
    return {"jobs": jobs.catalog()}


@app.get("/api/jobs", tags=["system"])
def jobs_recent(limit: int = Query(30, ge=1, le=200)) -> dict[str, object]:
    """Recent triggered jobs with status, newest first."""
    return jobs.recent(limit)


class JobRequest(BaseModel):
    key: str
    note: str = ""


@app.post("/api/jobs", tags=["system"])
def jobs_launch(body: JobRequest, actor: str = Depends(require_write("job.launch"))) -> dict[str, object]:
    """Gated write: launch a whitelisted runner as a background job. Audited."""
    result = jobs.launch(body.key, actor, body.note)
    audit.record_action(
        "job.launch", body.key, actor, reason=body.note,
        outcome="ok" if result.get("ok") else "error",
        details={"key": body.key, "job_id": (result.get("job") or {}).get("job_id"),
                 "error": result.get("error")},
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/jobs/{job_id}", tags=["system"])
def jobs_status(job_id: str) -> dict[str, object]:
    """One job's current status + log tail."""
    return jobs.status(job_id)


@app.get("/api/scenarios/macro/health", tags=["scenarios"])
def scenarios_macro_health() -> dict[str, object]:
    """MacroRAG status (SC-T3)."""
    return scenarios.macro_health()


@app.get("/api/scenarios/reverse", tags=["scenarios"])
def scenarios_reverse(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Reverse stress (S5): per scenario, the severity λ that drives loss to the limit."""
    return scenarios.reverse(valuation_date)


@app.get("/api/scenarios/reverse/{name}", tags=["scenarios"])
def scenarios_reverse_detail(name: str, valuation_date: str | None = Query(None)) -> dict[str, object]:
    """One scenario's reverse-stress detail incl. the materialized breaking-shock vector."""
    return scenarios.reverse_detail(name, valuation_date)


@app.get("/api/scenarios/ablation", tags=["scenarios"])
def scenarios_ablation(valuation_date: str | None = Query(None), severity: str | None = Query(None),
                       run_id: str | None = Query(None)) -> dict[str, object]:
    """Layer ablation (15f): per-scenario waterfall authored → + layers → final, grouped Shapley, cost per step."""
    return scenarios.ablation(valuation_date, severity, run_id)


@app.get("/api/scenarios/ablation/{name}", tags=["scenarios"])
def scenarios_ablation_detail(name: str, valuation_date: str | None = Query(None),
                              severity: str | None = Query(None),
                              run_id: str | None = Query(None)) -> dict[str, object]:
    """One scenario's layer-ablation waterfall plus the run-level fields."""
    return scenarios.ablation_scenario(name, valuation_date, severity, run_id)


@app.get("/api/scenarios/signal-health", tags=["scenarios"])
def scenarios_signal_health(valuation_date: str | None = Query(None),
                            run_id: str | None = Query(None)) -> dict[str, object]:
    """Signal health (15d/15f): per-source status worst first, held-out tiers, stopped scenarios, attention."""
    return scenarios.signal_health(valuation_date, run_id)


@app.get("/api/scenarios/measurement", tags=["scenarios"])
def scenarios_measurement(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Measurement: coverage and decision impact of the date's newest reports, plus the newest event replay."""
    return scenarios.measurement(valuation_date)


@app.get("/api/scenarios/{name}", tags=["scenarios"])
def scenarios_detail(name: str, severity: str | None = Query(None),
                     valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Scenario detail + shock derivation (SC-B2): drivers → propagation → overlays → vector."""
    return scenarios.detail(name, severity, valuation_date)


@app.get("/api/scenarios/{name}/results", tags=["scenarios"])
def scenarios_results(name: str, severity: str | None = Query(None),
                      valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Scenario results (SC-B3): total P&L, by asset class, per trade, capital."""
    return scenarios.results(name, severity, valuation_date)


@app.get("/api/scenarios/{name}/narrative", tags=["scenarios"])
def scenarios_narrative(name: str, severity: str | None = Query(None),
                        valuation_date: str | None = Query(None)) -> dict[str, object]:
    """LLM-generated scenario narrative (markdown) + generation cost, for source='llm' scenarios."""
    return scenarios.narrative(name, severity, valuation_date)


@app.get("/api/scenarios/{name}/attribution", tags=["scenarios"])
def scenarios_attribution(name: str,
                          valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Dual-RAG value-add: what MacroRAG + ARag each contributed vs the authored baseline + narrative."""
    return scenarios.attribution(name, valuation_date)


@app.get("/api/scenarios/{name}/trace", tags=["scenarios"])
def scenarios_trace(name: str, valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Captured call-trace for a dual-RAG scenario (slimmed list + per-service rollup)."""
    return scenarios.trace(name, valuation_date)


@app.get("/api/scenarios/{name}/trace/{seq}", tags=["scenarios"])
def scenarios_trace_call(name: str, seq: int,
                         valuation_date: str | None = Query(None)) -> dict[str, object]:
    """A single captured call — full request, response, and (LLM) system+user prompts."""
    return scenarios.trace_call(name, seq, valuation_date)


@app.get("/api/scenarios/{name}/cost", tags=["scenarios"])
def scenarios_cost(name: str, valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Per-call + per-service + total cost for a dual-RAG scenario."""
    return scenarios.cost(name, valuation_date)


# --- LLM cost & operations (Phase 2, stored-only) ------------------------- #


@app.get("/api/llmops/summary", tags=["llmops"])
def llmops_summary() -> dict[str, object]:
    """Operations rollup: LLM spend (manifests) + dual-RAG activity (traces) + budget."""
    return llmops.summary()


@app.get("/api/llmops/runs", tags=["llmops"])
def llmops_runs() -> dict[str, object]:
    """Index of LLM scenario-generation runs, newest first."""
    return llmops.runs()


@app.get("/api/llmops/traces", tags=["llmops"])
def llmops_traces() -> dict[str, object]:
    """Cross-run index of dual-RAG call traces captured on disk."""
    return llmops.traces()


@app.get("/api/llmops/traces/{run_id}", tags=["llmops"])
def llmops_trace_detail(run_id: str) -> dict[str, object]:
    """One dual-RAG trace's per-call list + rollup."""
    return llmops.trace_detail(run_id)


@app.get("/api/llmops/health", tags=["llmops"])
def llmops_health() -> dict[str, object]:
    """RAG service posture (MacroRAG + ARag) from config, with breaker settings."""
    return llmops.health()


@app.get("/api/llmops/runs/{run_id}", tags=["llmops"])
def llmops_run_detail(run_id: str) -> dict[str, object]:
    """One LLM run's cost rollup, per-stage tokens, models, and scenario names."""
    return llmops.run_detail(run_id)


# --- portfolio construction / position sizing (stored-only) --------------- #


@app.get("/api/sizing", tags=["sizing"])
def sizing_report(valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Sizing report: three risk-appetite profiles + PV/Risk/Capital re-valuation (stored)."""
    return sizing.report(valuation_date)


@app.get("/api/sizing/rebalance/{profile}", tags=["sizing"])
def sizing_rebalance(profile: str, valuation_date: str | None = Query(None)) -> dict[str, object]:
    """Rebalancing blotter: each position as current→target notional + signed delta (read-only)."""
    return sizing.rebalance(profile, valuation_date)


@app.get("/api/sizing/{profile}/variant", tags=["sizing"])
def sizing_variant(profile: str) -> dict[str, object]:
    """The generated book variant (portfolio_<profile>.json) for preview + client-side download."""
    return sizing.book_variant(profile)


@app.get("/api/sizing/active", tags=["sizing"])
def sizing_active() -> dict[str, object]:
    """Identity of the currently active book (portfolio.json)."""
    return sizing.active_book()


class ActivateRequest(BaseModel):
    profile: str
    note: str = ""


@app.post("/api/sizing/activate", tags=["sizing"])
def sizing_activate(
    body: ActivateRequest, actor: str = Depends(require_write("sizing.activate_variant")),
) -> dict[str, object]:
    """Gated write: promote a sized book variant to the active portfolio.json. Audited."""
    result = sizing.activate_variant(body.profile)
    audit.record_action(
        "sizing.activate_variant", body.profile, actor,
        reason=body.note, outcome="ok" if result.get("ok") else "error",
        details={"profile": body.profile, "previous_profile": result.get("previous_profile"),
                 "error": result.get("error")},
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/sizing/{profile}", tags=["sizing"])
def sizing_profile(profile: str, valuation_date: str | None = Query(None)) -> dict[str, object]:
    """One sizing profile (balanced | aggressive | risky): metrics + per-trade sizes."""
    return sizing.profile_detail(profile, valuation_date)
