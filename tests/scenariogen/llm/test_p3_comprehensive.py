"""P3 tests: Mode-1 (comprehensive) flow — discover → propose N → price all → keep top-N by loss.

Fully offline: a fake LLM backend scripted per phase (discover/evidence/propose/calibrate/narrate),
a fake MCP tool source, and a fake pricer that assigns a distinct P&L per candidate so the ranking
and top-N pruning are deterministic.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.pricer.config import get_project_root
from src.scenariogen.llm.config import load_llm_settings, load_mcp_settings
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.flow import LLMScenarioFlow
from src.scenariogen.llm.gateway import InstrumentedChatClient, RawCompletion, ToolCall
from src.scenariogen.llm.mcp.client import ServerSpec, ToolListing
from src.scenariogen.llm.mcp.registry import McpToolRegistry
from src.scenariogen.llm.prompts import PromptManager
from src.scenariogen.llm.results import PricedScenario
from src.scenariogen.llm.store import LLMScenarioArtifactStore

_DISCOVERY = json.dumps({
    "macro_factors": [{"factor_tag": "china_taiwan_tail", "relevance": "high"}],
    "company_factors": [{"ticker": "NVDA", "factor": "china_revenue"}],
    "summary": "Ranked factor universe: Taiwan supply, AI-capex cycle, rate-driven compression.",
})
_EVIDENCE = json.dumps({
    "company_factors": [{"ticker": "TSM", "factor": "foundry_concentration"}],
    "macro_factors": [{"factor_tag": "semi_sales", "relevance": "high"}],
    "summary": "Evidence: concentrated Taiwan/semis exposure; macro export-control risk.",
})
_PROPOSE = json.dumps({"scenarios": [
    {"name": "A_taiwan", "theme": "geo"},
    {"name": "B_ai_bust", "theme": "ai"},
    {"name": "C_rates", "theme": "rates"},
    {"name": "D_keyman", "theme": "keyman"},
]})
_NARRATIVE = "# Scenario\n\nNarrative body."


def _calibrated(name: str) -> str:
    return json.dumps({
        "name": name, "narrative": "n", "trigger": "t", "method": "conditional_covariance",
        "horizon_days": 10, "world_view_id": "macro-2026-06-24",
        "legs": [{"factor": "fx:TWD", "move": -0.08, "origin": "macro",
                  "citations": [{"chunk_id": "mc1", "source": "macro"}]}], "overlays": {},
    })


class CompFakeBackend:
    """Scripts each phase by a distinctive substring of its system prompt."""

    def create(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
               **_kwargs: Any) -> RawCompletion:
        system = str(messages[0]["content"]) if messages else ""
        tooled = any(m.get("role") == "tool" for m in messages)
        if tools and not tooled:
            if "identifying the stress-test risk factors" in system:
                return RawCompletion("", 50, 10, "tool_calls", (ToolCall("d1", "macro__macro_discover_factors", "{}"),))
            if "gathering grounded evidence" in system:
                return RawCompletion("", 50, 10, "tool_calls", (ToolCall("e1", "arag__narrative_search", "{}"),))
            if "concrete DRIVER shocks" in system:
                syn = ToolCall("c1", "macro__macro_synthesize_shock_vector", "{}")
                return RawCompletion("", 50, 10, "tool_calls", (syn,))
        if "identifying the stress-test risk factors" in system:
            return RawCompletion(_DISCOVERY, 80, 40, "stop")
        if "gathering grounded evidence" in system:
            return RawCompletion(_EVIDENCE, 80, 40, "stop")
        if "designing portfolio-level stress scenarios" in system:
            return RawCompletion(_PROPOSE, 80, 40, "stop")
        if "concrete DRIVER shocks" in system:
            user = str(messages[1]["content"]) if len(messages) > 1 else ""
            match = re.search(r'"name"\s*:\s*"([^"]+)"', user)  # the proposal name (first "name")
            return RawCompletion(_calibrated(match.group(1) if match else "unknown"), 80, 40, "stop")
        if "concise 1-2 page narrative" in system:
            return RawCompletion(_NARRATIVE, 80, 40, "stop")
        return RawCompletion("{}", 10, 1, "stop")


class CompFakeToolSource:
    def __init__(self) -> None:
        self._listings = {
            "arag": [ToolListing(n, n, {}) for n in ("coverage", "narrative_search", "facts_lookup", "facts_compute")],
            "macro": [ToolListing(n, n, {}) for n in
                      ("macro_discover_factors", "macro_vocabulary", "macro_query", "macro_get_citations",
                       "macro_shock_schema", "macro_synthesize_shock_vector")],
        }

    def list_tools(self, server: str) -> list[ToolListing]:
        return self._listings[server]

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        if tool == "macro_synthesize_shock_vector":  # a grounded synthesis (passes the grounding gate)
            return json.dumps({"shock_vector": {"USDTWD": {"value": 8.0, "unit": "pct", "chunk_ids": ["mc1"]}},
                               "world_view_id": "macro-2026-06-24", "chunks": [{"chunk_id": "mc1"}],
                               "synthesis_diagnostics": {"coverage_gap": False}})
        return "{}"


class CompFakePricer:
    PNL = {"A_taiwan": -1_000_000.0, "B_ai_bust": -5_000_000.0, "C_rates": -3_000_000.0, "D_keyman": -2_000_000.0}

    def __init__(self) -> None:
        self.priced: list[str] = []

    def price(self, definition: Any) -> PricedScenario:
        self.priced.append(definition.name)
        pnl = self.PNL.get(definition.name, 0.0)
        return PricedScenario(name=definition.name, total_pnl=pnl,
                              pnl_by_asset_class={"equity": pnl}, vector=dict(definition.drivers))


_SPECS = {
    "arag": ServerSpec(name="arag", command="py", args=[], tool_prefix="arag"),
    "macro": ServerSpec(name="macro", command="py", args=[], tool_prefix="macro"),
}


def test_comprehensive_flow_ranks_and_keeps_top_n(tmp_path) -> None:
    settings = load_llm_settings().with_budgets(max_candidates=4, max_scenarios=2)
    pricer = CompFakePricer()
    flow = LLMScenarioFlow(
        gateway=InstrumentedChatClient(CompFakeBackend(), CostTracker()),
        registry=McpToolRegistry(CompFakeToolSource(), _SPECS),
        pricer=pricer, store=LLMScenarioArtifactStore(tmp_path),
        prompts=PromptManager(get_project_root() / "prompts" / "scenariogen"),
        settings=settings, mcp_settings=load_mcp_settings(),
        portfolio_summary="AI book.", portfolio_tickers="NVDA, TSM", portfolio_asset_classes="equity, options",
    )
    artifact = flow.run_comprehensive(run_id="run-comp", created_at="2026-06-30")

    assert artifact.mode == "comprehensive"
    assert {p.name for p in artifact.proposals} == {"A_taiwan", "B_ai_bust", "C_rates", "D_keyman"}
    # all four candidates were priced
    assert sorted(pricer.priced) == ["A_taiwan", "B_ai_bust", "C_rates", "D_keyman"]
    # ranked most-negative-first; top-2 kept
    assert [r.name for r in artifact.ranking] == ["B_ai_bust", "C_rates", "D_keyman", "A_taiwan"]
    assert [r.kept for r in artifact.ranking] == [True, True, False, False]
    assert [r.total_pnl for r in artifact.ranking] == [-5_000_000.0, -3_000_000.0, -2_000_000.0, -1_000_000.0]
    # only the kept scenarios are calibrated-in-artifact and narrated
    assert [c.name for c in artifact.calibrated] == ["B_ai_bust", "C_rates"]
    assert artifact.world_view_id == "macro-2026-06-24"
    assert artifact.discovery.startswith("Ranked factor universe")
    # discover's structured factors (not just its prose summary) must reach the artifact,
    # unioned with evidence's — guards the discover JSON-shape/parse contract.
    assert {f.ticker for f in artifact.company_risk_factors} == {"NVDA", "TSM"}
    assert {f.factor_tag for f in artifact.macro_risk_factors} == {"china_taiwan_tail", "semi_sales"}

    narratives = LLMScenarioArtifactStore(tmp_path).run_dir("run-comp") / "narratives"
    assert {p.name for p in narratives.glob("*.md")} == {"b_ai_bust.md", "c_rates.md"}
    assert artifact.cost.total_cost_usd > 0
