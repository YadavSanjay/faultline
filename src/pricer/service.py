"""Stateless HTTP pricing service (P1b) — optional FastAPI facade over PricingEngine.

    uvicorn src.pricer.service:app --port 8080

The engine loads one immutable market-data snapshot (P0b) at startup; every request
prices against that snapshot and returns provenance-stamped results — the same engine
and code path as the EOD batch, so the service and batch never disagree. Workers are
stateless (snapshot pinned by id), so the service scales horizontally.

FastAPI/uvicorn are optional extras — see requirements-service.txt; the batch CLI does
not import this module.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

import QuantLib as ql
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import get_project_root, load_pricer_config
from .engine import PricingEngine
from .governance.golden import check_golden
from .governance.ipv import IpvEngine
from .governance.model_inventory import ModelInventory
from .obs import metrics
from .obs.run_manifest import RunManifestStore
from .output.result_store import ResultStore
from .portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from .portfolio.versioning import PortfolioStore
from .provenance import RunProvenance
from .risk.scenarios import ScenarioEngine, ScenarioReport, load_scenarios
from .risk.sensitivities import RiskEngine
from .risk.var import VarEngine
from .runner import _build_ctx
from .settings import get_settings

# Routes that stay open even when PRICER_API_KEY is set (liveness + Prometheus scrape).
_EXEMPT_PATHS = frozenset({"/health", "/metrics"})


class PriceRequest(BaseModel):
    trade_id: str
    valuation_date: date | None = None
    valuation_type: str = "PV"


class PortfolioRequest(BaseModel):
    asset_class: str | None = None
    valuation_date: date | None = None
    valuation_type: str = "PV"


class RiskRequest(BaseModel):
    trade_id: str | None = None
    asset_class: str | None = None
    valuation_date: date | None = None
    key_rates: bool = False


class ScenarioRequest(BaseModel):
    scenario: str
    asset_class: str | None = None
    valuation_date: date | None = None


def create_app() -> FastAPI:
    """Build the service with a warm engine (snapshot loaded once), rooted at the project root."""
    return create_app_at(get_project_root())


def create_app_at(output_root: Path, *, input_root: Path | None = None) -> FastAPI:
    """Build the same app as `create_app`, reading inputs from `input_root`, stores rooted at `output_root`.

    The engine reads market data from `input_root` (default: the project root); every store the
    app reads or writes (results, runs/manifests, portfolio versions, VaR/risk outputs) is rooted
    at `output_root`.

    Args:
        output_root: Root whose data base holds the app's stores.
        input_root: Root whose data base supplies market data; None means the project root.

    Returns:
        The FastAPI application.
    """
    config = load_pricer_config()
    in_root = input_root if input_root is not None else get_project_root()
    out_root = output_root
    engine = PricingEngine(config, in_root)
    store = ResultStore(out_root)
    risk = RiskEngine(engine, config)
    scen_engine = ScenarioEngine(engine)
    var_engine = VarEngine(risk, config, in_root)
    scenarios = load_scenarios(config)
    pf_cfg = load_portfolio_config()
    app = FastAPI(title="Faultline Pricer", version="1.0")

    @app.middleware("http")
    async def _require_api_key(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Enforce `X-API-Key` when PRICER_API_KEY is set (no-op otherwise; dev parity).

        Defense-in-depth behind the gateway/ALB (§2); /health + /metrics stay open for
        liveness + Prometheus scrape. Read per-request so a rotated key takes effect without
        a restart.
        """
        expected = get_settings().api_key
        if (expected and request.url.path not in _EXEMPT_PATHS
                and request.headers.get("x-api-key", "") != expected.get_secret_value()):
            return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
        return await call_next(request)

    def _ctx(vdate: date | None) -> Any:
        return _build_ctx(config, vdate or pf_cfg.default_valuation_date, "PV")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "snapshot_id": engine.snapshot_id,
            "quantlib_version": ql.__version__,
        }

    @app.post("/price")
    def price(req: PriceRequest) -> dict[str, Any]:
        trades = load_portfolio(trade_id=req.trade_id)
        if not trades:
            raise HTTPException(status_code=404, detail=f"trade not found: {req.trade_id}")
        vdate = req.valuation_date or pf_cfg.default_valuation_date
        ctx = _build_ctx(config, vdate, req.valuation_type)
        try:
            result = engine.price(trades[0], ctx, RunProvenance.capture(in_root, config))
        except Exception as exc:  # surface pricing/data errors as 422, not 500
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result.to_dict()

    @app.post("/price-portfolio")
    def price_portfolio(req: PortfolioRequest) -> dict[str, Any]:
        trades = load_portfolio(asset_class=req.asset_class)
        if not trades:
            raise HTTPException(status_code=404, detail="no trades selected")
        vdate = req.valuation_date or pf_cfg.default_valuation_date
        ctx = _build_ctx(config, vdate, req.valuation_type)
        outcomes = engine.price_portfolio(trades, ctx)
        return {
            "snapshot_id": engine.snapshot_id,
            "outcomes": [
                {
                    "trade_id": o.trade_id,
                    "status": o.status,
                    "currency": o.currency,
                    "pv": o.result.pv if o.result is not None else None,
                    "warnings": o.warnings,
                    "result": o.result.to_dict() if o.result is not None else None,
                }
                for o in outcomes
            ],
        }

    # -- results store (P1c): historical / aggregate reads ------------------- #
    @app.get("/results/pv-history/{trade_id}")
    def pv_history(trade_id: str) -> dict[str, Any]:
        return {"trade_id": trade_id,
                "history": store.pv_history(trade_id).to_dict(orient="records")}

    @app.get("/results/book/{valuation_date}")
    def book(valuation_date: str) -> dict[str, Any]:
        return {"valuation_date": valuation_date,
                "by_asset_class": store.book_pv_by_asset_class(valuation_date).to_dict(orient="records")}

    @app.get("/results/run/{valuation_date}")
    def run_results(valuation_date: str) -> dict[str, Any]:
        return {"valuation_date": valuation_date,
                "trades": store.latest_run(valuation_date).to_dict(orient="records")}

    # -- risk (P2): sensitivities / scenarios / VaR ------------------------- #
    @app.post("/risk/sensitivities")
    def risk_sensitivities(req: RiskRequest) -> dict[str, Any]:
        if not req.trade_id:
            raise HTTPException(status_code=422, detail="trade_id is required")
        trades = load_portfolio(trade_id=req.trade_id)
        if not trades:
            raise HTTPException(status_code=404, detail=f"trade not found: {req.trade_id}")
        return risk.sensitivities(trades[0], _ctx(req.valuation_date),
                                  key_rates=req.key_rates).to_dict()

    @app.post("/risk/scenario")
    def risk_scenario(req: ScenarioRequest) -> dict[str, Any]:
        scen = scenarios.get(req.scenario)
        if scen is None:
            raise HTTPException(status_code=404, detail=f"unknown scenario: {req.scenario}")
        ctx = _ctx(req.valuation_date)
        results = scen_engine.run(scen, load_portfolio(asset_class=req.asset_class), ctx)
        return ScenarioReport(scen.name, scen.description, ctx.valuation_date, results).to_dict()

    @app.post("/risk/var")
    def risk_var(req: RiskRequest) -> dict[str, Any]:
        ctx = _ctx(req.valuation_date)
        return var_engine.historical_var(load_portfolio(asset_class=req.asset_class), ctx).to_dict()

    # -- observability (P3): Prometheus scrape + run-manifest audit trail ---- #
    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        return Response(content=metrics.exposition(), media_type=metrics.content_type())

    @app.get("/runs")
    def recent_runs(limit: int = 20) -> dict[str, Any]:
        return {"runs": RunManifestStore(out_root).recent(limit).to_dict(orient="records")}

    @app.get("/portfolio/versions")
    def portfolio_versions(limit: int = 50) -> dict[str, Any]:
        return {"versions": PortfolioStore(out_root).versions(limit).to_dict(orient="records")}

    # -- governance (P4): model inventory + golden/IPV verification ---------- #
    @app.get("/governance/model-inventory")
    def model_inventory() -> dict[str, Any]:
        return ModelInventory.load().to_dict()

    @app.post("/governance/verify")
    def governance_verify(req: PortfolioRequest) -> dict[str, Any]:
        ctx = _ctx(req.valuation_date)
        trades = load_portfolio(asset_class=req.asset_class)
        pvs = {o.trade_id: o.result.pv
               for o in engine.price_portfolio(trades, ctx) if o.result is not None}
        golden = check_golden(pvs, engine.snapshot_id)
        ipv = IpvEngine(engine).verify(trades, ctx)
        covered, missing = ModelInventory.load().coverage([t.trade_type.value for t in trades])
        return {
            "passed": golden.passed and ipv.all_passed and not missing,
            "golden": golden.to_dict(), "ipv": ipv.to_dict(),
            "model_inventory": {"covered": covered, "missing": missing},
        }

    return app


app = create_app()
