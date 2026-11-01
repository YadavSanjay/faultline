"""LLM scenario-generation flow (P2 hypothetical + P3 comprehensive).

The deterministic skeleton that wires the phases for one generation run, bound to a `RunTrace` so
cost/tokens/tool-calls roll up automatically.

- Mode 2 (`run_hypothetical`): for each free-text theme, gather evidence → propose one scenario →
  synthesize macro shocks (code-made MacroRAG call) → calibrate correlated driver shocks → verify →
  fact-check → grounding gate → price → narrate → persist.
- Mode 1 (`run_comprehensive`): discover the ranked risk-factor universe → gather evidence →
  propose up to `max_candidates` portfolio-level scenarios → synthesize + calibrate + gate + price
  each → keep the top-N by max loss → narrate the kept ones → persist (with the full candidate
  ranking).

A scenario the grounding gate refuses (spec llm-grounding-gate) is never priced, kept or
published; it is recorded in `LLMScenarioArtifact.rejected` and the run carries on.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import LLMSettings
from .gateway import InstrumentedChatClient
from .grounding import assess_grounding, world_view_id_of
from .mcp.registry import McpToolRegistry
from .phases import (
    CoverageView,
    FactCheckConfig,
    calibrate_scenario,
    discover_factor_universe,
    fact_check,
    fetch_shock_schema,
    gather_evidence,
    narrate_scenario,
    propose_scenario,
    propose_scenarios,
    verify_scenario,
)
from .phases.calibrate import SYNTHESIZE_TOOL, synthesis_for_model, synthesize_macro_shocks
from .prompts import PromptManager
from .provider import LLMScenarioProvider, slugify
from .results import PricedScenario, PricingService
from .schema import (
    CalibratedScenario,
    CostSummary,
    LLMScenarioArtifact,
    ScenarioProposal,
    ScenarioRanking,
    ScenarioRejection,
)
from .store import LLMScenarioArtifactStore
from .trace import RunTrace, run_trace

logger = logging.getLogger("scenariogen.llm.flow")

SYNTHESIS_FAILED = "synthesis_failed"  # rejection reason: the code-made MacroRAG call failed


class ScenarioRefusedError(Exception):
    """A scenario failed its MacroRAG synthesis or the grounding gate; it must not be priced."""

    def __init__(self, rejection: ScenarioRejection):
        super().__init__(f"{rejection.name!r} refused: {', '.join(rejection.reasons)}")
        self.rejection = rejection


def _cost_summary(trace: RunTrace) -> CostSummary:
    summary = trace.summary()
    per_stage = {
        stage: {
            "n_calls": float(rollup["n_calls"]),
            "prompt_tokens": float(rollup["prompt_tokens"]),
            "completion_tokens": float(rollup["completion_tokens"]),
            "cost_usd": float(rollup["cost_usd"]),
        }
        for stage, rollup in summary["per_stage"].items()
    }
    return CostSummary(
        total_cost_usd=float(summary["total_cost_usd"]),
        total_prompt_tokens=int(summary["total_prompt_tokens"]),
        total_completion_tokens=int(summary["total_completion_tokens"]),
        n_llm_calls=int(summary["n_llm_calls"]),
        n_tool_calls=int(summary["n_tool_calls"]),
        per_stage=per_stage,
    )


class LLMScenarioFlow:
    """Orchestrates the LLM scenario-generation phases for one run."""

    def __init__(
        self,
        *,
        gateway: InstrumentedChatClient,
        registry: McpToolRegistry,
        pricer: PricingService,
        store: LLMScenarioArtifactStore,
        prompts: PromptManager,
        settings: LLMSettings,
        mcp_settings: dict[str, Any],
        portfolio_summary: str = "",
        portfolio_tickers: str = "",
        portfolio_asset_classes: str = "",
    ):
        self.gateway = gateway
        self.registry = registry
        self.pricer = pricer
        self.store = store
        self.prompts = prompts
        self.settings = settings
        self.mcp_settings = mcp_settings
        self.portfolio_summary = portfolio_summary
        self.portfolio_tickers = portfolio_tickers
        self.portfolio_asset_classes = portfolio_asset_classes
        self.provider = LLMScenarioProvider()
        self._fact_check_cfg = FactCheckConfig()
        self._coverage_view: CoverageView | None = None

    # -- allowlists ---------------------------------------------------------- #
    def _allow(self, phase: str) -> set[str]:
        return set(self.mcp_settings.get("phase_allowlists", {}).get(phase, []))

    def _evidence_allow(self) -> set[str]:
        # Mode 2 evidence may also discover relevant companies/factors for the theme.
        return self._allow("discover") | self._allow("evidence")

    # -- deterministic fact-check (F4) --------------------------------------- #
    def _coverage(self) -> CoverageView:
        """ARag coverage ground truth, fetched once per run (best-effort; empty if unavailable)."""
        if self._coverage_view is None:
            self._coverage_view = CoverageView.empty()
            if "arag__coverage" in self.registry.names():
                try:
                    self._coverage_view = CoverageView.from_tool_json(
                        self.registry.dispatch("arag__coverage", {}))
                except Exception as exc:  # coverage is a best-effort ground-truth aid, never fatal
                    logger.warning("fact_check: ARag coverage unavailable (%s)", exc)
        return self._coverage_view

    def _as_of_year(self) -> int | None:
        vdate = getattr(self.pricer, "valuation_date", None)
        return vdate.year if vdate is not None else None

    def _fact_checked(self, calibration: CalibratedScenario) -> CalibratedScenario:
        """Deterministic company-leg fact-check: clamp out-of-band moves, flag grounding/coverage."""
        return fact_check(
            calibration, coverage=self._coverage(),
            as_of_year=self._as_of_year(), config=self._fact_check_cfg,
        ).calibrated

    def _calibrate_grounded(
        self, *, proposal: ScenarioProposal, evidence: str, shock_schema: str
    ) -> CalibratedScenario:
        """Synthesize → calibrate → verify → fact-check → grounding gate, for one proposal.

        Args:
            proposal: The scenario proposal to calibrate.
            evidence: The grounding evidence text for the proposal.
            shock_schema: The pre-fetched macro shock schema.

        Returns:
            The calibrated scenario, carrying the synthesis's world_view_id.

        Raises:
            ScenarioRefusedError: The synthesis call failed or the gate refused the scenario.
        """
        try:
            synthesis = synthesize_macro_shocks(self.registry, proposal)
        except Exception as exc:  # isolation point: a failed synthesis refuses only this scenario
            raise ScenarioRefusedError(
                ScenarioRejection(name=proposal.name, reasons=[SYNTHESIS_FAILED], detail=str(exc))) from exc
        calibration = calibrate_scenario(
            gateway=self.gateway, registry=self.registry, prompts=self.prompts,
            settings=self.settings, proposal=proposal, evidence=evidence,
            # The code made the one synthesize call; the model does not get to make another.
            allow=self._allow("calibrate") - {SYNTHESIZE_TOOL}, shock_schema=shock_schema,
            macro_synthesis=synthesis,
        )
        calibration = verify_scenario(  # P7: drop ungrounded legs; macro legs are judged on the synthesis
            gateway=self.gateway, prompts=self.prompts, settings=self.settings, calibrated=calibration,
            evidence=f"{evidence}\n\nMacroRAG shock-vector synthesis:\n{synthesis_for_model(synthesis)}",
        ).calibrated
        calibration = self._fact_checked(calibration)  # F4: deterministic company-leg check
        # The world-view id is the synthesis's, never the model's (and never a placeholder).
        calibration = calibration.model_copy(update={"world_view_id": world_view_id_of(synthesis)})
        verdict = assess_grounding(calibration, synthesis=synthesis, seen_ids=self.registry.seen_ids())
        if not verdict.ok:
            raise ScenarioRefusedError(ScenarioRejection(name=calibration.name, reasons=list(verdict.reasons)))
        return calibration

    def _refuse(self, run_id: str, rejected: list[ScenarioRejection], exc: ScenarioRefusedError) -> None:
        """Logs a refused scenario and records it for the manifest."""
        logger.error("[%s] %s; not priced or published %s", run_id, exc,
                     f"({exc.rejection.detail})" if exc.rejection.detail else "")
        rejected.append(exc.rejection)

    # -- public API ---------------------------------------------------------- #
    def run_hypothetical(self, themes: list[str], *, run_id: str, created_at: str = "") -> LLMScenarioArtifact:
        proposals: list[ScenarioProposal] = []
        calibrated: list[CalibratedScenario] = []
        priced_results: list[PricedScenario] = []
        rejected: list[ScenarioRejection] = []
        artifact: LLMScenarioArtifact | None = None
        with run_trace(run_id) as trace:
            shock_schema = fetch_shock_schema(self.registry)
            company_factors = []
            macro_factors = []
            budget = self.settings.budgets.max_total_cost_usd
            for theme in themes:
                if trace.total_cost_usd >= budget:
                    logger.warning("[%s] cost budget $%.2f reached; stopping (%d scenario(s) done)",
                                   run_id, budget, len(calibrated))
                    break
                logger.info("[%s] theme: %s", run_id, theme)
                pre = _checkpoint(trace)
                evidence = gather_evidence(
                    gateway=self.gateway, registry=self.registry, prompts=self.prompts,
                    settings=self.settings, allow=self._evidence_allow(), scope=theme, focus=theme,
                )
                company_factors.extend(evidence.company_factors)
                macro_factors.extend(evidence.macro_factors)
                proposal = propose_scenario(
                    gateway=self.gateway, prompts=self.prompts, settings=self.settings,
                    intent=f"Design a stress scenario for the hypothetical theme: {theme}.",
                    portfolio_summary=self.portfolio_summary, evidence=evidence.text,
                )
                proposals.append(proposal)
                try:
                    calibration = self._calibrate_grounded(
                        proposal=proposal, evidence=evidence.text, shock_schema=shock_schema)
                except ScenarioRefusedError as exc:
                    self._refuse(run_id, rejected, exc)
                    continue
                calibrated.append(calibration)
                definition = self.provider.to_definition(calibration)
                priced = self.pricer.price(definition)
                priced_results.append(priced)
                derivation_cost = _delta(trace, pre)  # cost to derive this scenario (excludes narrate)
                narrative = narrate_scenario(
                    gateway=self.gateway, prompts=self.prompts, settings=self.settings,
                    calibrated=calibration, priced=priced, cost=derivation_cost,
                )
                self.store.write_narrative(run_id, slugify(calibration.name), narrative)
                logger.info("[%s] %s priced: P&L=%s", run_id, calibration.name, f"{priced.total_pnl:,.0f}")

            world_view_id = next((c.world_view_id for c in calibrated if c.world_view_id), "")
            artifact = LLMScenarioArtifact(
                run_id=run_id,
                mode="hypothetical",
                created_at=created_at,
                models=dict(self.settings.model_tiers),
                llm=self.settings.run_record(),
                world_view_id=world_view_id,
                prompt_sig=self.prompts.signature(),
                proposals=proposals,
                calibrated=calibrated,
                rejected=rejected,
                company_risk_factors=company_factors,
                macro_risk_factors=macro_factors,
                cost=_cost_summary(trace),
            )
            self.store.write_manifest(artifact)
        logger.info("[%s] done: %d scenario(s), cost $%.4f", run_id, len(calibrated), artifact.cost.total_cost_usd)
        return artifact

    def run_comprehensive(self, *, run_id: str, created_at: str = "") -> LLMScenarioArtifact:
        """Mode 1: discover the factor universe → propose candidates → price all → keep top-N by max loss."""
        budgets = self.settings.budgets
        artifact: LLMScenarioArtifact | None = None
        with run_trace(run_id) as trace:
            shock_schema = fetch_shock_schema(self.registry)

            # A. Discover the ranked risk-factor universe for the portfolio.
            discovery = discover_factor_universe(
                gateway=self.gateway, registry=self.registry, prompts=self.prompts, settings=self.settings,
                allow=self._allow("discover"), portfolio_summary=self.portfolio_summary,
                tickers=self.portfolio_tickers, asset_classes=self.portfolio_asset_classes,
            )
            logger.info("[%s] discover: %d macro / %d company factor(s)", run_id,
                        len(discovery.macro_factors), len(discovery.company_factors))

            # B. Gather grounded evidence across the discovered factor universe.
            evidence = gather_evidence(
                gateway=self.gateway, registry=self.registry, prompts=self.prompts, settings=self.settings,
                allow=self._evidence_allow(), scope="the whole AI-themed portfolio", focus=discovery.text,
            )
            combined = f"{discovery.text}\n\n{evidence.text}"

            # C. Propose up to max_candidates portfolio-level scenarios.
            candidates = propose_scenarios(
                gateway=self.gateway, prompts=self.prompts, settings=self.settings,
                intent="Design the most material portfolio-level stress scenarios these factors imply, "
                       "ordered by likely severity.",
                portfolio_summary=self.portfolio_summary, evidence=combined,
                max_scenarios=budgets.max_candidates,
            )
            logger.info("[%s] proposed %d candidate scenario(s)", run_id, len(candidates))

            # D. Calibrate + verify + price each candidate (per-candidate fault isolation).
            rows: list[tuple[ScenarioProposal, CalibratedScenario, PricedScenario]] = []
            rejected: list[ScenarioRejection] = []
            budget = budgets.max_total_cost_usd
            for candidate in candidates:
                if trace.total_cost_usd >= budget:
                    logger.warning("[%s] cost budget $%.2f reached; stopping after %d candidate(s)",
                                   run_id, budget, len(rows))
                    break
                try:
                    calibration = self._calibrate_grounded(
                        proposal=candidate, evidence=combined, shock_schema=shock_schema)
                    priced = self.pricer.price(self.provider.to_definition(calibration))
                except ScenarioRefusedError as exc:
                    self._refuse(run_id, rejected, exc)
                    continue
                except Exception as exc:
                    logger.warning("[%s] candidate %r failed calibrate/price: %s", run_id, candidate.name, exc)
                    continue
                rows.append((candidate, calibration, priced))
                logger.info("[%s] candidate %s priced: P&L=%s", run_id, calibration.name, f"{priced.total_pnl:,.0f}")

            # E. Rank by max loss (most negative P&L first) and keep the top-N.
            rows.sort(key=lambda row: row[2].total_pnl)
            keep = rows[: budgets.max_scenarios]
            kept_names = {row[1].name for row in keep}

            # F. Narrate the kept scenarios.
            for _candidate, calibration, priced in keep:
                narrative = narrate_scenario(
                    gateway=self.gateway, prompts=self.prompts, settings=self.settings,
                    calibrated=calibration, priced=priced, cost=_cost_summary(trace).model_dump(),
                )
                self.store.write_narrative(run_id, slugify(calibration.name), narrative)

            ranking = [
                ScenarioRanking(name=cal.name, total_pnl=pr.total_pnl, kept=cal.name in kept_names)
                for _cand, cal, pr in rows
            ]
            world_view_id = next((cal.world_view_id for _c, cal, _p in keep if cal.world_view_id), "")
            artifact = LLMScenarioArtifact(
                run_id=run_id, mode="comprehensive", created_at=created_at,
                models=dict(self.settings.model_tiers), llm=self.settings.run_record(),
                world_view_id=world_view_id,
                prompt_sig=self.prompts.signature(), discovery=discovery.text,
                proposals=candidates, calibrated=[cal for _c, cal, _p in keep], ranking=ranking,
                rejected=rejected,
                company_risk_factors=discovery.company_factors + evidence.company_factors,
                macro_risk_factors=discovery.macro_factors + evidence.macro_factors,
                cost=_cost_summary(trace),
            )
            self.store.write_manifest(artifact)
        logger.info("[%s] done: %d candidate(s), kept top %d, cost $%.4f",
                    run_id, len(ranking), len(keep), artifact.cost.total_cost_usd)
        return artifact


def _checkpoint(trace: RunTrace) -> tuple[float, int, int, int, int]:
    return (
        trace.total_cost_usd,
        trace.total_prompt_tokens,
        trace.total_completion_tokens,
        trace.n_llm_calls,
        trace.n_tool_calls,
    )


def _delta(trace: RunTrace, pre: tuple[float, int, int, int, int]) -> dict[str, Any]:
    return {
        "total_cost_usd": round(trace.total_cost_usd - pre[0], 6),
        "prompt_tokens": trace.total_prompt_tokens - pre[1],
        "completion_tokens": trace.total_completion_tokens - pre[2],
        "n_llm_calls": trace.n_llm_calls - pre[3],
        "n_tool_calls": trace.n_tool_calls - pre[4],
    }
