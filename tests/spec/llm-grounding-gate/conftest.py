"""Offline fakes and a flow harness for the llm-grounding-gate spec tests.

Fakes sit only at the three I/O boundaries the spec allows (docs/specs/llm-grounding-gate.md, Notes):
the LLM ``ChatBackend``, the MCP ``ToolSource`` and the ``PricingService``. Everything else is real:
``InstrumentedChatClient``, ``McpToolRegistry``, ``PromptManager`` over ``prompts/scenariogen``,
``load_llm_settings()`` / ``load_mcp_settings()`` and an ``LLMScenarioArtifactStore`` under a tmp dir.

A test describes each scenario with a ``Scen``. The fake LLM backend is scripted per phase by a phrase
of that phase's system prompt; the fake MCP source answers ``macro_synthesize_shock_vector`` with a
per-scenario MacroRAG-shaped response; both append to one shared ``events`` list so call order can be
checked (spec C3). The scenario a call belongs to is recognised by the scenario's name, which is
embedded in every proposal field (name, theme, narrative, trigger, rationale); a synthesize call whose
arguments carry no name is attributed to the next not-yet-synthesized scenario in proposal order.

Chunk ids: the evidence phase's ``arag__narrative_search`` returns ``ARAG_CHUNK``; scenario ``k``'s
synthesis returns ``m-<k>-1``. A grounded scenario's macro leg cites its own ``m-<k>-1`` and its equity
leg cites ``ARAG_CHUNK``, so every citation was returned by a tool before (or at) its calibration.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.pricer.config import get_project_root
from src.scenariogen.llm.config import load_llm_settings, load_mcp_settings
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.flow import LLMScenarioFlow
from src.scenariogen.llm.gateway import InstrumentedChatClient, RawCompletion, ToolCall
from src.scenariogen.llm.mcp.client import ServerSpec, ToolListing
from src.scenariogen.llm.mcp.registry import McpToolRegistry
from src.scenariogen.llm.prompts import PromptManager
from src.scenariogen.llm.results import PricedScenario
from src.scenariogen.llm.schema import LLMScenarioArtifact
from src.scenariogen.llm.store import LLMScenarioArtifactStore

# Phase phrases from prompts/scenariogen/*_system.txt (spec Notes).
DISCOVER = "identifying the stress-test risk factors"
EVIDENCE = "gathering grounded evidence"
PROPOSE = "designing portfolio-level stress scenarios"
CALIBRATE = "concrete DRIVER shocks"
VERIFY = "faithfulness checker"
NARRATE = "concise 1-2 page narrative"

SYNTH_TOOL = "macro_synthesize_shock_vector"
ARAG_CHUNK = "arag-chunk-7f3e"
PNL_PER_UNIT_MOVE = 1e8  # fake pricer: total P&L = 1e8 * sum of driver moves (synthetic, deterministic)
PLACEHOLDER_WV = "macro-worldview-missing-2026-10-11 (MacroRAG degraded)"

# Distinct driver legs per scenario (canonical keys, decimal moves) so verify verdicts and P&L are
# scenario-specific. Equity moves stay inside fact-check's [-0.95, 0.95] band (no clamping).
LEGS: dict[str, list[tuple[str, float, str]]] = {
    "alpha": [("fx:TWD", -0.08, "macro"), ("eq:NVDA", -0.20, "equity")],
    "beta": [("fx:JPY", 0.05, "macro"), ("eq:TSM", -0.25, "equity")],
    "gamma": [("ir:DGS10", 0.0075, "macro"), ("eq:AMD", -0.15, "equity")],
    "delta": [("credit", 0.01, "macro"), ("eq:AVGO", -0.10, "equity")],
}

_SPECS = {
    "arag": ServerSpec(name="arag", command="py", args=[], tool_prefix="arag"),
    "macro": ServerSpec(name="macro", command="py", args=[], tool_prefix="macro"),
}

_EVIDENCE_JSON = json.dumps({
    "company_factors": [{"ticker": "NVDA", "factor": "china_revenue", "description": "export controls",
                         "citations": [{"chunk_id": ARAG_CHUNK, "source": "arag"}]}],
    "macro_factors": [{"factor_tag": "china_taiwan_tail", "relevance": "high", "rationale": "blockade risk"}],
    "summary": "Semis supply-chain concentration; FX and rates stress.",
})
_DISCOVERY_JSON = json.dumps({
    "macro_factors": [{"factor_tag": "china_taiwan_tail", "relevance": "high"}],
    "company_factors": [{"ticker": "NVDA", "factor": "china_revenue"}],
    "summary": "Ranked factor universe: Taiwan supply, AI-capex cycle, rates.",
})


@dataclass
class Scen:
    """One scenario's script.

    synth: "ok" | "coverage_gap" (flag true, empty vector) | "empty_vector" (flag false, {}) |
        "missing_wv" (no world_view_id key) | "raise" | "not_json" | "json_list".
    model_wv: the world_view_id the model writes in its calibrate JSON (None = key omitted).
    """

    key: str
    synth: str = "ok"
    wv: str = ""
    model_wv: str | None = PLACEHOLDER_WV
    all_unsupported: bool = False
    uncited: bool = False
    unseen: bool = False

    @property
    def name(self) -> str:
        return f"scen_{self.key}"

    @property
    def world_view(self) -> str:
        return self.wv or f"macro-wv-{self.key}-2026-10-01"

    @property
    def macro_chunk(self) -> str:
        return f"m-{self.key}-1"

    @property
    def expected_pnl(self) -> float:
        return PNL_PER_UNIT_MOVE * sum(m for _f, m, _o in LEGS[self.key])

    def proposal(self) -> dict[str, Any]:
        n = self.name
        return {"name": n, "theme": f"{n} theme", "narrative": f"{n}: a stress on {self.key} exposures.",
                "trigger": f"{n} trigger", "method": "conditional_covariance", "horizon_days": 10,
                "driver_factors": [f for f, _m, _o in LEGS[self.key]], "exposed_trades": [],
                "rationale": f"{n} rationale"}

    def synthesis(self) -> dict[str, Any]:
        """A MacroRAG synthesize response (shape from spec Notes)."""
        c = self.macro_chunk
        body: dict[str, Any] = {
            "answer": "Synthesized macro shock vector.",
            "chunks": [{"chunk_id": c, "text": "Macro corpus passage."}],
            "shock_vector": {"USDTWD": {"value": 8.0, "unit": "pct", "chunk_ids": [c], "grounding_mode": "direct"}},
            "world_view_id": self.world_view,
            "synthesis_diagnostics": {"outcome": "grounded", "coverage_gap": False, "kept_fields": ["USDTWD"]},
        }
        if self.synth == "coverage_gap":
            body["shock_vector"] = {}
            body["synthesis_diagnostics"] = {"outcome": "coverage_gap", "coverage_gap": True, "kept_fields": []}
        elif self.synth == "empty_vector":
            body["shock_vector"] = {}
        elif self.synth == "missing_wv":
            del body["world_view_id"]
        return body

    def calibrate_json(self) -> str:
        legs = []
        for i, (factor, move, origin) in enumerate(LEGS[self.key]):
            if origin == "macro":
                cid, src = (f"m-{self.key}-ghost", "macro") if self.unseen else (self.macro_chunk, "macro")
            else:
                cid, src = ARAG_CHUNK, "arag"
            cites = [] if (self.uncited and i == len(LEGS[self.key]) - 1) else [{"chunk_id": cid, "source": src}]
            legs.append({"factor": factor, "move": move, "origin": origin, "rationale": f"{factor} driver",
                         "citations": cites})
        obj: dict[str, Any] = {"name": self.name, "narrative": f"{self.name} calibrated.", "trigger": "t",
                               "method": "conditional_covariance", "horizon_days": 10, "legs": legs,
                               "overlays": {}}
        if self.model_wv is not None:
            obj["world_view_id"] = self.model_wv
        return json.dumps(obj)


def _non_system_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages if m.get("role") != "system")


@dataclass
class FakeBackend:
    """``ChatBackend`` fake: scripted per phase, logs every call into the shared event list."""

    scens: list[Scen]
    events: list[tuple[Any, ...]]

    def _named(self, text: str) -> list[Scen]:
        return [s for s in self.scens if s.name in text]

    def create(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
               **_kw: Any) -> RawCompletion:
        system = str(messages[0].get("content") or "") if messages else ""
        self.events.append(("llm", system, copy.deepcopy(messages)))
        tooled = any(m.get("role") == "tool" for m in messages)
        user = _non_system_text(messages)
        if DISCOVER in system:
            if tools and not tooled:
                return RawCompletion("", 50, 10, "tool_calls", (ToolCall("d1", "macro__macro_discover_factors", "{}"),))
            return RawCompletion(_DISCOVERY_JSON, 50, 20, "stop")
        if EVIDENCE in system:
            if tools and not tooled:
                return RawCompletion("", 50, 10, "tool_calls", (ToolCall("e1", "arag__narrative_search", "{}"),))
            return RawCompletion(_EVIDENCE_JSON, 50, 20, "stop")
        if PROPOSE in system:
            named = self._named(user)
            chosen = named if len(named) == 1 else self.scens  # Mode 2: one theme; Mode 1: all candidates
            return RawCompletion(json.dumps({"scenarios": [s.proposal() for s in chosen]}), 50, 20, "stop")
        if CALIBRATE in system:
            named = self._named(user)
            assert len(named) == 1, f"fake calibrate could not tell the scenario apart: {[s.name for s in named]}"
            return RawCompletion(named[0].calibrate_json(), 50, 20, "stop")
        if VERIFY in system:
            unsupported = {f for s in self.scens if s.all_unsupported for f, _m, _o in LEGS[s.key]}
            factors = dict.fromkeys(re.findall(r'"factor"\s*:\s*"([^"]+)"', user))
            verdicts = [{"factor": f, "verdict": "UNSUPPORTED" if f in unsupported else "SUPPORTED",
                         "reason": "scripted"} for f in factors]
            return RawCompletion(json.dumps({"verdicts": verdicts}), 30, 10, "stop")
        if NARRATE in system:
            return RawCompletion("# Scenario\n\nNarrative.", 50, 20, "stop")
        return RawCompletion("{}", 10, 1, "stop")


@dataclass
class FakeToolSource:
    """MCP ``ToolSource`` fake with the real tool names; logs every call into the shared event list."""

    scens: list[Scen]
    events: list[tuple[Any, ...]]
    synthesized: list[str] = field(default_factory=list)

    def list_tools(self, server: str) -> list[ToolListing]:
        names = {
            "arag": ("coverage", "narrative_search", "facts_lookup", "facts_compute"),
            "macro": ("macro_discover_factors", "macro_vocabulary", "macro_query", "macro_get_citations",
                      "macro_shock_schema", SYNTH_TOOL),
        }[server]
        return [ToolListing(n, n, {}) for n in names]

    def _synth_target(self, arguments: dict[str, Any]) -> Scen:
        text = json.dumps(arguments, default=str)
        named = [s for s in self.scens if s.name in text]
        if len(named) == 1:
            return named[0]
        pending = [s for s in self.scens if s.name not in self.synthesized]
        assert pending, "fake MCP: more synthesize calls than scenarios"
        return pending[0]

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        self.events.append(("tool", tool, copy.deepcopy(arguments)))
        if tool == "narrative_search":
            return json.dumps({"chunks": [{"chunk_id": ARAG_CHUNK, "text": "Export controls on China sales.",
                                           "section_path": "10-K/Item 1A"}]})
        if tool == "macro_shock_schema":
            return json.dumps({"fields": [{"name": "USDTWD", "units": ["pct"], "preferred_unit": "pct"}]})
        if tool == SYNTH_TOOL:
            scen = self._synth_target(arguments)
            self.synthesized.append(scen.name)
            if scen.synth == "raise":
                raise TimeoutError("MCP call timed out (scripted)")
            if scen.synth == "not_json":
                return "upstream error: synthesis unavailable"
            if scen.synth == "json_list":
                return json.dumps([scen.synthesis()])
            return json.dumps(scen.synthesis())
        return "{}"


@dataclass
class FakePricer:
    """``PricingService`` fake: P&L = PNL_PER_UNIT_MOVE * sum of driver moves; records each call."""

    priced: list[tuple[str, float]] = field(default_factory=list)

    def price(self, definition: Any) -> PricedScenario:
        pnl = PNL_PER_UNIT_MOVE * sum(float(v) for v in definition.drivers.values())
        self.priced.append((definition.name, pnl))
        return PricedScenario(name=definition.name, total_pnl=pnl, pnl_by_asset_class={"all": pnl},
                              vector=dict(definition.drivers))


@dataclass
class Run:
    artifact: LLMScenarioArtifact
    events: list[tuple[Any, ...]]
    pricer: FakePricer

    @property
    def priced_names(self) -> list[str]:
        return [n for n, _p in self.pricer.priced]

    @property
    def kept_names(self) -> list[str]:
        return [c.name for c in self.artifact.calibrated]

    def calibrate_calls(self) -> list[tuple[int, list[dict[str, Any]]]]:
        return [(i, e[2]) for i, e in enumerate(self.events) if e[0] == "llm" and CALIBRATE in e[1]]

    def synth_calls(self) -> list[tuple[int, dict[str, Any]]]:
        return [(i, e[2]) for i, e in enumerate(self.events) if e[0] == "tool" and e[1] == SYNTH_TOOL]


def _flow(root: Path, scens: list[Scen]) -> tuple[LLMScenarioFlow, list[tuple[Any, ...]], FakePricer]:
    events: list[tuple[Any, ...]] = []
    pricer = FakePricer()
    flow = LLMScenarioFlow(
        gateway=InstrumentedChatClient(FakeBackend(scens, events), CostTracker()),
        registry=McpToolRegistry(FakeToolSource(scens, events), _SPECS),
        pricer=pricer, store=LLMScenarioArtifactStore(root),
        prompts=PromptManager(get_project_root() / "prompts" / "scenariogen"),
        settings=load_llm_settings(), mcp_settings=load_mcp_settings(),
        portfolio_summary="Synthetic AI-themed book.", portfolio_tickers="NVDA, TSM, AMD, AVGO",
        portfolio_asset_classes="equity, fx, rates, credit",
    )
    return flow, events, pricer


def run_mode2(root: Path, scens: list[Scen], run_id: str = "run-spec-m2") -> Run:
    """Mode 2: one theme per scenario, in list order (each theme carries the scenario's name)."""
    flow, events, pricer = _flow(root, scens)
    artifact = flow.run_hypothetical([s.proposal()["theme"] for s in scens], run_id=run_id,
                                     created_at="2026-10-05")
    return Run(artifact, events, pricer)


def run_mode1(root: Path, scens: list[Scen], run_id: str = "run-spec-m1") -> Run:
    """Mode 1: the fake propose reply lists every scenario as a candidate (fewer than max_candidates)."""
    settings = load_llm_settings()
    assert len(scens) <= settings.budgets.max_candidates, "precondition: all candidates fit the budget"
    assert len(scens) <= settings.budgets.max_scenarios, "precondition: top-N keeps every priced candidate"
    flow, events, pricer = _flow(root, scens)
    artifact = flow.run_comprehensive(run_id=run_id, created_at="2026-10-05")
    return Run(artifact, events, pricer)


@pytest.fixture(scope="session")
def gg() -> SimpleNamespace:
    """The scenario script type, the two flow runners and the shared constants."""
    return SimpleNamespace(Scen=Scen, run_mode1=run_mode1, run_mode2=run_mode2, LEGS=LEGS,
                           ARAG_CHUNK=ARAG_CHUNK, PLACEHOLDER_WV=PLACEHOLDER_WV, SYNTH_TOOL=SYNTH_TOOL,
                           CALIBRATE=CALIBRATE)
