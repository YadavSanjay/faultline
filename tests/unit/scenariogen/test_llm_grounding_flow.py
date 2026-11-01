"""Unit tests for the implementer-defined parts of llm-grounding-gate.

The locked spec tests (tests/spec/llm-grounding-gate/) cover the gate rules and the flow contract.
These cover what the spec left to the implementation: the refusal record on the manifest, the
registry's run-level id record, the synthesis text the model sees, and the tool loop's allowlist.
Fakes sit only at the LLM backend and MCP tool-source boundaries.
"""

from __future__ import annotations

import json
from typing import Any

from src.pricer.config import get_project_root
from src.scenariogen.llm.config import load_llm_settings, load_mcp_settings
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.flow import SYNTHESIS_FAILED, LLMScenarioFlow
from src.scenariogen.llm.gateway import InstrumentedChatClient, RawCompletion, ToolCall
from src.scenariogen.llm.mcp.client import ServerSpec, ToolListing
from src.scenariogen.llm.mcp.loop import run_tool_loop
from src.scenariogen.llm.mcp.registry import McpToolRegistry
from src.scenariogen.llm.phases.calibrate import synthesis_for_model
from src.scenariogen.llm.prompts import PromptManager
from src.scenariogen.llm.results import PricedScenario
from src.scenariogen.llm.store import LLMScenarioArtifactStore

_SPECS = {
    "arag": ServerSpec(name="arag", command="py", args=[], tool_prefix="arag"),
    "macro": ServerSpec(name="macro", command="py", args=[], tool_prefix="macro"),
}
_SYNTH = "macro_synthesize_shock_vector"


class _Source:
    """MCP tool source: narrative search returns chunk a1; synthesize behaves per `synth`."""

    def __init__(self, synth: str = "ok") -> None:
        self.synth = synth
        self.calls: list[str] = []

    def list_tools(self, server: str) -> list[ToolListing]:
        names = {"arag": ("narrative_search",), "macro": ("macro_shock_schema", _SYNTH)}[server]
        return [ToolListing(n, n, {}) for n in names]

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        self.calls.append(tool)
        if tool == "narrative_search":
            return json.dumps({"chunks": [{"chunk_id": "a1"}]})
        if tool == _SYNTH:
            if self.synth == "raise":
                raise TimeoutError("MCP 'macro' call exceeded 300s")
            vector = {} if self.synth == "gap" else {"USDTWD": {"value": 8.0, "unit": "pct", "chunk_ids": ["m1"]}}
            return json.dumps({"shock_vector": vector, "world_view_id": "wv-1", "chunks": [{"chunk_id": "m1"}]})
        return "{}"


class _Backend:
    """LLM backend scripted per phase; calibrate cites m1 (macro) and a1 (ARag)."""

    def create(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
               **_kw: Any) -> RawCompletion:
        system = str(messages[0]["content"])
        if "gathering grounded evidence" in system:
            if tools and not any(m.get("role") == "tool" for m in messages):
                return RawCompletion("", 1, 1, "tool_calls", (ToolCall("e", "arag__narrative_search", "{}"),))
            return RawCompletion(json.dumps({"summary": "evidence"}), 1, 1, "stop")
        if "designing portfolio-level stress scenarios" in system:
            return RawCompletion(json.dumps({"scenarios": [{"name": "taiwan", "narrative": "blockade"}]}), 1, 1, "stop")
        if "concrete DRIVER shocks" in system:
            return RawCompletion(json.dumps({"name": "taiwan", "legs": [
                {"factor": "fx:TWD", "move": -0.08, "origin": "macro", "citations": [{"chunk_id": "m1"}]},
                {"factor": "eq:NVDA", "move": -0.2, "origin": "equity", "citations": [{"chunk_id": "a1"}]}]}),
                1, 1, "stop")
        return RawCompletion("{}", 1, 1, "stop")


class _Pricer:
    def price(self, definition: Any) -> PricedScenario:
        return PricedScenario(name=definition.name, total_pnl=-1.0, pnl_by_asset_class={})


def _run(tmp_path: Any, synth: str) -> Any:
    flow = LLMScenarioFlow(
        gateway=InstrumentedChatClient(_Backend(), CostTracker()),
        registry=McpToolRegistry(_Source(synth), _SPECS), pricer=_Pricer(),
        store=LLMScenarioArtifactStore(tmp_path),
        prompts=PromptManager(get_project_root() / "prompts" / "scenariogen"),
        settings=load_llm_settings(), mcp_settings=load_mcp_settings(),
    )
    return flow.run_hypothetical(["taiwan"], run_id="run-unit", created_at="2026-10-05")


def test_refusal_is_recorded_with_gate_reasons_and_persisted(tmp_path) -> None:
    artifact = _run(tmp_path, "gap")
    assert artifact.calibrated == []
    assert [(r.name, r.reasons) for r in artifact.rejected] == [("taiwan", ["coverage_gap"])]
    manifest = json.loads(next(tmp_path.rglob("manifest.json")).read_text(encoding="utf-8"))
    assert manifest["rejected"][0]["reasons"] == ["coverage_gap"]


def test_failed_synthesis_is_recorded_with_the_error(tmp_path) -> None:
    artifact = _run(tmp_path, "raise")
    assert artifact.rejected[0].reasons == [SYNTHESIS_FAILED]
    assert "exceeded 300s" in artifact.rejected[0].detail


def test_grounded_scenario_is_kept_with_no_rejection(tmp_path) -> None:
    artifact = _run(tmp_path, "ok")
    assert [c.name for c in artifact.calibrated] == ["taiwan"]
    assert artifact.rejected == []
    assert artifact.world_view_id == "wv-1"


def test_registry_records_ids_from_every_tool_output() -> None:
    registry = McpToolRegistry(_Source(), _SPECS)
    assert registry.seen_ids() == frozenset()
    registry.dispatch("arag__narrative_search", {})
    registry.dispatch(f"macro__{_SYNTH}", {})
    assert registry.seen_ids() == frozenset({"a1", "m1"})


def test_synthesis_for_model_withholds_world_view_id() -> None:
    text = synthesis_for_model({"world_view_id": "wv-1", "shock_vector": {"USDTWD": {"chunk_ids": ["m1"]}}})
    assert "world_view_id" not in text
    assert "m1" in text
    assert synthesis_for_model(None) == "(no MacroRAG synthesis available)"


def test_tool_loop_refuses_a_tool_outside_the_allowlist() -> None:
    source = _Source()

    class _Caller:
        def __init__(self) -> None:
            self.turn = 0

        def create(self, **_kw: Any) -> RawCompletion:
            self.turn += 1
            if self.turn == 1:
                return RawCompletion("", 1, 1, "tool_calls", (ToolCall("c", f"macro__{_SYNTH}", "{}"),))
            return RawCompletion("done", 1, 1, "stop")

    result = run_tool_loop(
        gateway=InstrumentedChatClient(_Caller(), CostTracker()),
        registry=McpToolRegistry(source, _SPECS), system="s", user="u", model="gpt-5-mini",
        stage="calibrate", allow={"macro__macro_shock_schema"},
    )
    assert source.calls == []  # the disallowed synthesize call never reached MCP
    assert "not available" in result.messages[-1]["content"]
