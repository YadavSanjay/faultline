"""LLM scenario-generation CLI (P2 hypothetical + P3 comprehensive).

Wires the real dependencies — OpenAI gpt-5 gateway, the ARag + MacroRAG MCP servers (stdio),
and the full-reval pricing service — and runs a generation flow end to end:

    # Mode 2 — hypothetical (themed):
    python -m src.scenariogen.llm.runner --mode hypothetical --themes "Taiwan geopolitics" "AI boom-bust"

    # Mode 1 — comprehensive (discover the portfolio's risk factors, keep the top-N by max loss):
    python -m src.scenariogen.llm.runner --mode comprehensive --no-capital

Requires OPENAI_API_KEY and the two MCP servers to be launchable (see configs/scenariogen/llm/mcp.json).
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.portfolio.portfolio_loader import load_portfolio_config
from src.pricer.runner import _setup_logging
from src.scenariogen.llm.config import load_llm_settings, load_mcp_settings
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.flow import LLMScenarioFlow
from src.scenariogen.llm.gateway import InstrumentedChatClient
from src.scenariogen.llm.mcp.client import McpClient, parse_server_specs
from src.scenariogen.llm.mcp.registry import McpToolRegistry
from src.scenariogen.llm.mcp.timeouts import CallTimeoutPolicy
from src.scenariogen.llm.pricing import ScenarioPricingService
from src.scenariogen.llm.profiles import build_chat_backend, check_api_keys
from src.scenariogen.llm.prompts import PromptManager
from src.scenariogen.llm.provider import LLMScenarioProvider, slugify
from src.scenariogen.llm.schema import CalibratedScenario, LLMScenarioArtifact
from src.scenariogen.llm.store import LLMScenarioArtifactStore
from src.scenariogen.report import StressReport, write_stress_report

logger = logging.getLogger("scenariogen.llm.runner")


def _llm_provenance(artifact: LLMScenarioArtifact, cal: CalibratedScenario) -> dict[str, object]:
    """The per-scenario `llm` provenance block stamped onto the StressReport (surfaced by BFF/UI)."""
    return {
        "run_id": artifact.run_id,
        "mode": artifact.mode,
        "models": dict(artifact.models),
        "llm_profile": artifact.llm.get("profile", ""),
        "world_view_id": cal.world_view_id,
        "narrative": f"{slugify(cal.name)}.md",
        "prompt_sig": artifact.prompt_sig,
        "trigger": cal.trigger,
        "total_cost_usd": round(artifact.cost.total_cost_usd, 6),
        "n_llm_calls": artifact.cost.n_llm_calls,
        "n_tool_calls": artifact.cost.n_tool_calls,
        "legs": [
            {"factor": leg.factor, "origin": leg.origin, "n_citations": len(leg.citations)}
            for leg in cal.legs
        ],
    }


def _write_stress_report(
    root: Path, pricer: ScenarioPricingService, artifact: LLMScenarioArtifact) -> Path | str | None:
    """Re-price the artifact's kept scenarios and write a StressReport tagged source='llm'.

    This is what makes LLM scenarios appear in the existing BFF/UI (data/pricer/scenario/<date>/):
    each scenario entry carries `source: "llm"` + the `llm` provenance block.
    """
    if not artifact.calibrated:
        return None
    provider = LLMScenarioProvider()
    results = []
    for cal in artifact.calibrated:
        try:
            result = pricer.price_result(provider.to_definition(cal))
        except Exception as exc:
            logger.warning("stress-report: skipping %r: %s", cal.name, exc)
            continue
        result.source = "llm"
        result.llm = _llm_provenance(artifact, cal)
        results.append(result)
    if not results:
        return None
    report = StressReport(
        valuation_date=pricer.valuation_date.isoformat(), run_id=artifact.run_id,
        snapshot_id=pricer.snapshot_id, covariance=pricer.covariance_dict, results=results,
    )
    out = write_stress_report(report, root)
    logger.info("Wrote LLM StressReport (%d scenario(s)) -> %s", len(results), out)
    return out


def _portfolio_facets(asset_class: str | None = None) -> tuple[str, str, str]:
    """(summary, tickers, asset_classes) for the discover/propose prompts, from the raw config."""
    raw = load_portfolio_config().raw
    by_class: dict[str, int] = {}
    tickers: list[str] = []
    n = 0
    for trade in raw.get("trades", []):
        ac = str(trade.get("asset_class", ""))
        if asset_class and ac != asset_class:
            continue
        n += 1
        by_class[ac] = by_class.get(ac, 0) + 1
        params = trade.get("params", {})
        underlier = params.get("ticker") or params.get("underlier") or ""
        if underlier:
            tickers.append(str(underlier))
    classes = ", ".join(f"{k}:{v}" for k, v in sorted(by_class.items()))
    names = ", ".join(sorted(set(tickers)))
    summary = f"{n} trades ({classes}). Underliers: {names}."
    return summary, names, ", ".join(sorted(by_class))


def _run_id() -> str:
    return f"llm-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


def _execute(
    *,
    mode: str,
    themes: list[str],
    valuation_date: date | None,
    asset_class: str | None,
    with_capital: bool,
    var_lookback: int | None,
    cov_lookback: int | None,
    llm_profile: str | None = None,
) -> LLMScenarioArtifact:
    root = get_project_root()
    settings = load_llm_settings(root, profile=llm_profile)
    check_api_keys(settings)  # fail before any MCP server starts if a provider in use has no key
    mcp_settings = load_mcp_settings(root)
    prompts = PromptManager(root / "prompts" / "scenariogen")
    gateway = InstrumentedChatClient(
        build_chat_backend(settings), CostTracker(settings.pricing),
        max_attempts=settings.retry.max_attempts,
        min_wait_s=settings.retry.min_wait_s, max_wait_s=settings.retry.max_wait_s,
        cost_budget_usd=settings.budgets.max_total_cost_usd,
    )
    config = load_pricer_config()
    vdate = valuation_date or load_portfolio_config().default_valuation_date
    pricer = ScenarioPricingService(
        root=root, config=config, valuation_date=vdate, asset_class=asset_class,
        with_capital=with_capital, var_lookback=var_lookback, cov_lookback=cov_lookback,
    )
    store = LLMScenarioArtifactStore(root)
    summary, tickers, classes = _portfolio_facets(asset_class)
    run_id = _run_id()
    specs = parse_server_specs(mcp_settings)
    timeout_s = float(mcp_settings.get("timeout_s", 600))
    policy = CallTimeoutPolicy.from_settings(mcp_settings)  # per-tool timeouts; raises on a bad value

    logger.info("LLM scenario run %s mode=%s profile=%s | snapshot=%s cov=%s",
                run_id, mode, settings.profile, pricer.snapshot_id, pricer.cov_id)
    with McpClient(specs, timeout_s=timeout_s, policy=policy) as mcp_client:
        registry = McpToolRegistry(mcp_client, specs)
        logger.info("MCP tools available: %s", ", ".join(registry.names()))
        flow = LLMScenarioFlow(
            gateway=gateway, registry=registry, pricer=pricer, store=store,
            prompts=prompts, settings=settings, mcp_settings=mcp_settings,
            portfolio_summary=summary, portfolio_tickers=tickers, portfolio_asset_classes=classes,
        )
        created = datetime.now(UTC).isoformat()
        artifact = (
            flow.run_comprehensive(run_id=run_id, created_at=created)
            if mode == "comprehensive"
            else flow.run_hypothetical(themes, run_id=run_id, created_at=created)
        )
    logger.info("Wrote artifacts -> %s", store.run_dir(run_id))
    _write_stress_report(root, pricer, artifact)  # surface the scenarios in the existing BFF/UI
    return artifact


def run_hypothetical(
    themes: list[str],
    *,
    valuation_date: date | None = None,
    asset_class: str | None = None,
    with_capital: bool = True,
    var_lookback: int | None = None,
    cov_lookback: int | None = None,
    llm_profile: str | None = None,
) -> LLMScenarioArtifact:
    return _execute(
        mode="hypothetical", themes=themes, valuation_date=valuation_date, asset_class=asset_class,
        with_capital=with_capital, var_lookback=var_lookback, cov_lookback=cov_lookback,
        llm_profile=llm_profile,
    )


def run_comprehensive(
    *,
    valuation_date: date | None = None,
    asset_class: str | None = None,
    with_capital: bool = True,
    var_lookback: int | None = None,
    cov_lookback: int | None = None,
    llm_profile: str | None = None,
) -> LLMScenarioArtifact:
    return _execute(
        mode="comprehensive", themes=[], valuation_date=valuation_date, asset_class=asset_class,
        with_capital=with_capital, var_lookback=var_lookback, cov_lookback=cov_lookback,
        llm_profile=llm_profile,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="scenariogen LLM scenario generator")
    p.add_argument("--mode", choices=["hypothetical", "comprehensive"], default="hypothetical")
    p.add_argument("--themes", nargs="+", default=None, help="free-text themes (required for --mode hypothetical)")
    p.add_argument("--valuation-date", default=None, help="ISO date; default: portfolio default")
    p.add_argument("--asset-class", default=None, help="restrict the priced book to one asset class")
    p.add_argument("--no-capital", action="store_true", help="skip ES-under-shock capital (faster)")
    p.add_argument("--var-lookback", type=int, default=None)
    p.add_argument("--cov-lookback", type=int, default=None)
    p.add_argument("--llm-profile", default=None,
                   help="provider profile from llm.json (default: $SCENARIOGEN_LLM_PROFILE or openai)")
    return p.parse_args()


if __name__ == "__main__":
    _setup_logging()
    args = _parse_args()
    vdate = date.fromisoformat(args.valuation_date) if args.valuation_date else None
    if args.mode == "comprehensive":
        run_comprehensive(
            valuation_date=vdate, asset_class=args.asset_class, with_capital=not args.no_capital,
            var_lookback=args.var_lookback, cov_lookback=args.cov_lookback, llm_profile=args.llm_profile,
        )
    else:
        if not args.themes:
            raise SystemExit("--themes is required for --mode hypothetical")
        run_hypothetical(
            args.themes, valuation_date=vdate, asset_class=args.asset_class,
            with_capital=not args.no_capital, var_lookback=args.var_lookback, cov_lookback=args.cov_lookback,
            llm_profile=args.llm_profile,
        )
