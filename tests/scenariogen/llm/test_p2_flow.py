"""P2 tests: Mode-2 (hypothetical) flow end-to-end.

The flow is exercised fully offline with a fake LLM backend (scripted per phase by system-prompt
keyword) + a fake MCP tool source + a fake pricer. A separate test prices a hand-built definition
through the REAL `ScenarioPricingService` to validate the LLM→engine bridge.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.portfolio.portfolio_loader import load_portfolio_config
from src.scenariogen.llm.config import load_llm_settings, load_mcp_settings
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.flow import LLMScenarioFlow
from src.scenariogen.llm.gateway import InstrumentedChatClient, RawCompletion, ToolCall
from src.scenariogen.llm.mcp.client import ServerSpec, ToolListing
from src.scenariogen.llm.mcp.registry import McpToolRegistry
from src.scenariogen.llm.phases.parsing import extract_json_object
from src.scenariogen.llm.prompts import PromptManager
from src.scenariogen.llm.provider import LLMScenarioProvider, slugify
from src.scenariogen.llm.results import PricedScenario
from src.scenariogen.llm.schema import CalibratedScenario, DriverLeg
from src.scenariogen.llm.store import LLMScenarioArtifactStore

# --- scripted LLM outputs --------------------------------------------------- #
_EVIDENCE_JSON = json.dumps(
    {
        "company_factors": [
            {"ticker": "NVDA", "factor": "china_revenue", "description": "data-center revenue at risk",
             "citations": [{"chunk_id": "a1", "source": "arag"}]}
        ],
        "macro_factors": [
            {"factor_tag": "china_taiwan_tail", "relevance": "high", "rationale": "export controls",
             "citations": [{"chunk_id": "c1", "source": "macro"}]}
        ],
        "summary": "Taiwan tail risk: NVDA/TSM exposed; TWD and credit stress.",
    }
)
_PROPOSE_JSON = json.dumps(
    {
        "scenarios": [
            {"name": "taiwan_geopolitical", "theme": "geopolitics",
             "narrative": "Cross-strait escalation hits AI supply chain.", "trigger": "blockade",
             "method": "conditional_covariance", "horizon_days": 10,
             "driver_factors": ["fx:TWD", "eq:NVDA"], "exposed_trades": ["TRADE_1", "TRADE_2"],
             "rationale": "Concentrated Taiwan/semis exposure."}
        ]
    }
)
_CALIBRATE_JSON = json.dumps(
    {
        "name": "taiwan_geopolitical", "narrative": "Cross-strait escalation.", "trigger": "blockade",
        "method": "conditional_covariance", "horizon_days": 10, "world_view_id": "macro-2026-06-18",
        "legs": [
            {"factor": "fx:TWD", "move": -0.08, "origin": "macro",
             "citations": [{"chunk_id": "c1", "source": "macro"}]},
            {"factor": "eq:NVDA", "move": -0.20, "origin": "equity",
             "citations": [{"chunk_id": "a1", "source": "arag"}]},
        ],
        "overlays": {},
    }
)
_NARRATIVE_MD = "# Taiwan Geopolitical\n\nDerivation narrative with cost."


class FlowFakeBackend:
    """Scripts per-phase output by a distinctive substring of each phase's system prompt."""

    def create(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
               **_kwargs: Any) -> RawCompletion:
        system = str(messages[0]["content"]) if messages else ""
        already_tooled = any(m.get("role") == "tool" for m in messages)
        if tools and not already_tooled:
            if "gathering grounded evidence" in system:
                return RawCompletion("", 100, 10, "tool_calls", (ToolCall("c1", "arag__narrative_search", "{}"),))
            if "concrete DRIVER shocks" in system:
                synth = ToolCall("c2", "macro__macro_synthesize_shock_vector", "{}")
                return RawCompletion("", 100, 10, "tool_calls", (synth,))
        if "gathering grounded evidence" in system:
            return RawCompletion(_EVIDENCE_JSON, 80, 40, "stop")
        if "designing portfolio-level stress scenarios" in system:
            return RawCompletion(_PROPOSE_JSON, 80, 40, "stop")
        if "concrete DRIVER shocks" in system:
            return RawCompletion(_CALIBRATE_JSON, 80, 40, "stop")
        if "concise 1-2 page narrative" in system:
            return RawCompletion(_NARRATIVE_MD, 80, 40, "stop")
        return RawCompletion("{}", 10, 1, "stop")


class FlowFakeToolSource:
    def __init__(self) -> None:
        # Real names: ARag tools are bare; MacroRAG tools are themselves macro_*-prefixed.
        self._listings = {
            "arag": [ToolListing(n, n, {}) for n in ("coverage", "narrative_search", "facts_lookup", "facts_compute")],
            "macro": [ToolListing(n, n, {}) for n in
                      ("macro_discover_factors", "macro_vocabulary", "macro_query", "macro_get_citations",
                       "macro_shock_schema", "macro_synthesize_shock_vector")],
        }

    def list_tools(self, server: str) -> list[ToolListing]:
        return self._listings[server]

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        if tool == "macro_shock_schema":
            return json.dumps({"fields": [{"name": "USDTWD", "units": ["pct"], "preferred_unit": "pct"}]})
        if tool == "macro_synthesize_shock_vector":
            return json.dumps({"shock_vector": {"USDTWD": {"value": 8.0, "unit": "pct", "chunk_ids": ["c1"]}},
                               "world_view_id": "macro-2026-06-18", "chunks": [{"chunk_id": "c1"}]})
        if tool == "narrative_search":
            return json.dumps({"chunks": [{"chunk_id": "a1", "text": "export controls on China sales"}]})
        return "{}"


class FakePricer:
    def __init__(self) -> None:
        self.priced: list[Any] = []

    def price(self, definition: Any) -> PricedScenario:
        self.priced.append(definition)
        return PricedScenario(
            name=definition.name, total_pnl=-1_250_000.0,
            pnl_by_asset_class={"equity": -1_000_000.0, "fx": -250_000.0},
            capital_delta=500_000.0, es_base=2_000_000.0, es_shocked=2_500_000.0,
            plausibility_status="flag", vector=dict(definition.drivers),
        )


_SPECS = {
    "arag": ServerSpec(name="arag", command="py", args=[], tool_prefix="arag"),
    "macro": ServerSpec(name="macro", command="py", args=[], tool_prefix="macro"),
}


# --------------------------------------------------------------------------- #
# parsing + provider
# --------------------------------------------------------------------------- #
def test_extract_json_object_variants() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json_object('Here you go:\n{"a": 3}\nThanks') == {"a": 3}
    # trailing prose after the JSON object (the "Extra data" case from the live run)
    assert extract_json_object('{"a": 4} — let me know if you need changes.') == {"a": 4}
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json_object("no json here")


def test_provider_to_definition_and_slug() -> None:
    cal = CalibratedScenario(name="Taiwan Tail!", legs=[DriverLeg(factor="fx:TWD", move=-0.08)])
    definition = LLMScenarioProvider().to_definition(cal)
    assert definition.drivers["fx:TWD"] == pytest.approx(-0.08)
    assert definition.severity == "llm" and definition.severity_scale == 1.0
    assert slugify("Taiwan Tail!") == "taiwan_tail"
    with pytest.raises(ValueError, match="no driver legs"):
        LLMScenarioProvider().to_definition(CalibratedScenario(name="empty"))


# --------------------------------------------------------------------------- #
# full Mode-2 flow (offline)
# --------------------------------------------------------------------------- #
def _flow(store_root: Any, pricer: FakePricer) -> LLMScenarioFlow:
    registry = McpToolRegistry(FlowFakeToolSource(), _SPECS)
    gateway = InstrumentedChatClient(FlowFakeBackend(), CostTracker())
    return LLMScenarioFlow(
        gateway=gateway, registry=registry, pricer=pricer,
        store=LLMScenarioArtifactStore(store_root),
        prompts=PromptManager(get_project_root() / "prompts" / "scenariogen"),
        settings=load_llm_settings(), mcp_settings=load_mcp_settings(),
        portfolio_summary="AI-themed book.",
    )


def test_hypothetical_flow_end_to_end(tmp_path) -> None:
    pricer = FakePricer()
    flow = _flow(tmp_path, pricer)
    artifact = flow.run_hypothetical(["Taiwan geopolitics"], run_id="run-test", created_at="2026-06-30")

    # one scenario, calibrated + priced
    assert artifact.mode == "hypothetical"
    assert [c.name for c in artifact.calibrated] == ["taiwan_geopolitical"]
    assert [p.name for p in artifact.proposals] == ["taiwan_geopolitical"]
    assert artifact.world_view_id == "macro-2026-06-18"
    assert artifact.company_risk_factors[0].ticker == "NVDA"

    # the calibrated driver legs reached the pricer as a ScenarioDefinition
    assert len(pricer.priced) == 1
    assert pricer.priced[0].drivers["fx:TWD"] == pytest.approx(-0.08)
    assert pricer.priced[0].drivers["eq:NVDA"] == pytest.approx(-0.20)

    # cost + tool usage recorded (evidence search + calibrate synthesize + shock-schema prefetch)
    assert artifact.cost.total_cost_usd > 0
    assert artifact.cost.n_tool_calls >= 2
    assert {"evidence", "propose", "calibrate", "narrate"} <= set(artifact.cost.per_stage)

    # narrative + manifest persisted
    narrative = LLMScenarioArtifactStore(tmp_path).run_dir("run-test") / "narratives" / "taiwan_geopolitical.md"
    assert narrative.exists() and "Taiwan" in narrative.read_text(encoding="utf-8")
    assert LLMScenarioArtifactStore(tmp_path).read_manifest("run-test").run_id == "run-test"


# --------------------------------------------------------------------------- #
# real pricing-service bridge (light: rates only, no capital)
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_real_pricing_service_prices_llm_definition() -> None:
    from src.scenariogen.llm.pricing import ScenarioPricingService

    root = get_project_root()
    vdate = load_portfolio_config().default_valuation_date
    service = ScenarioPricingService(
        root=root, config=load_pricer_config(), valuation_date=vdate,
        asset_class="rates", with_capital=False,
    )
    cal = CalibratedScenario(name="rates_up", legs=[DriverLeg(factor="ir:DGS10", move=0.005, origin="macro")])
    priced = service.price(LLMScenarioProvider().to_definition(cal))
    assert priced.name == "rates_up"
    assert isinstance(priced.total_pnl, float)
    assert "ir:DGS10" in priced.vector
