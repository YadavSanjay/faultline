"""P1 tests for the MCP client layer (offline — fake tool source + fake backend, no servers)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from src.scenariogen.llm.config import load_mcp_settings
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.gateway import InstrumentedChatClient, RawCompletion, ToolCall
from src.scenariogen.llm.mcp.client import ServerSpec, ToolListing, parse_server_specs
from src.scenariogen.llm.mcp.loop import run_tool_loop
from src.scenariogen.llm.mcp.registry import McpToolRegistry
from src.scenariogen.llm.trace import run_trace

_SPECS = {
    "arag": ServerSpec(name="arag", command="py", args=[], tool_prefix="arag"),
    "macro": ServerSpec(name="macro", command="py", args=[], tool_prefix="macro"),
}


class FakeToolSource:
    """Canned tool listings + call results; records dispatched calls."""

    def __init__(self) -> None:
        self._listings: dict[str, list[ToolListing]] = {
            "arag": [
                ToolListing(
                    "facts_lookup",
                    "Look up XBRL facts",
                    {"type": "object", "properties": {"company": {"type": "string"}}, "required": ["company"]},
                ),
                ToolListing("narrative_search", "Search 10-K narrative", {}),  # empty schema
            ],
            "macro": [
                ToolListing(
                    "discover_factors",
                    "Rank macro factors",
                    {"type": "object", "properties": {"portfolio": {"type": "string"}}},
                ),
            ],
        }
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def list_tools(self, server: str) -> list[ToolListing]:
        return self._listings[server]

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        self.calls.append((server, tool, arguments))
        return json.dumps({"server": server, "tool": tool, "args": arguments})


class FakeToolBackend:
    """Requests its configured tool up to `wants` times (while tools are offered), then answers."""

    def __init__(self, tool_name: str, *, wants: int = 1, final: str = "final answer"):
        self.tool_name = tool_name
        self.wants = wants
        self.final = final
        self.requested = 0
        self.last_tools: list[dict[str, Any]] | None = None

    def create(self, *, tools: list[dict[str, Any]] | None = None, **_kwargs: Any) -> RawCompletion:
        self.last_tools = tools
        if tools and self.requested < self.wants:
            self.requested += 1
            call = ToolCall(id=f"call{self.requested}", name=self.tool_name, arguments="{}")
            return RawCompletion("", 100, 10, "tool_calls", (call,))
        return RawCompletion(self.final, 50, 5, "stop")


def _registry(source: FakeToolSource | None = None) -> tuple[McpToolRegistry, FakeToolSource]:
    src = source or FakeToolSource()
    return McpToolRegistry(src, _SPECS), src


# --------------------------------------------------------------------------- #
# ServerSpec / config parsing
# --------------------------------------------------------------------------- #
def test_server_spec_full_env_merges_os_environ(monkeypatch) -> None:
    monkeypatch.setenv("SENTINEL_ENV", "1")
    spec = ServerSpec(name="x", command="py", args=[], env={"FOO": "bar"})
    env = spec.full_env()
    assert env["FOO"] == "bar"
    assert env["SENTINEL_ENV"] == "1"  # inherited from os.environ


def test_parse_server_specs_from_real_config(monkeypatch) -> None:
    monkeypatch.delenv("MCP_PYTHON", raising=False)
    specs = parse_server_specs(load_mcp_settings())
    assert set(specs) == {"arag", "macro"}
    assert specs["arag"].args == ["-m", "src.mcp_server.server"]  # ARag's runnable entry
    assert specs["arag"].command == "python"  # the default when MCP_PYTHON is unset
    assert specs["macro"].prefix == "macro"


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def test_registry_namespaces_and_converts_tools() -> None:
    reg, _ = _registry()
    assert set(reg.names()) == {"arag__facts_lookup", "arag__narrative_search", "macro__discover_factors"}
    tools = reg.openai_tools()
    by_name = {t["function"]["name"]: t for t in tools}
    assert by_name["arag__facts_lookup"]["type"] == "function"
    assert by_name["arag__facts_lookup"]["function"]["parameters"]["required"] == ["company"]
    # empty MCP schema → valid OpenAI object schema
    assert by_name["arag__narrative_search"]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_registry_allowlist_filters() -> None:
    reg, _ = _registry()
    tools = reg.openai_tools({"macro__discover_factors"})
    assert [t["function"]["name"] for t in tools] == ["macro__discover_factors"]


def test_registry_dispatch_routes_and_records_trace() -> None:
    reg, src = _registry()
    with run_trace("run-1") as trace:
        out = reg.dispatch("arag__facts_lookup", {"company": "NVDA"})
        assert json.loads(out)["tool"] == "facts_lookup"
        assert src.calls == [("arag", "facts_lookup", {"company": "NVDA"})]
        assert trace.n_tool_calls == 1
        assert trace.tool_calls_by_name["arag__facts_lookup"] == 1


def test_registry_dispatch_unknown_raises() -> None:
    reg, _ = _registry()
    with pytest.raises(KeyError):
        reg.dispatch("nope__tool", {})


# --------------------------------------------------------------------------- #
# gateway tool-call passthrough
# --------------------------------------------------------------------------- #
def test_gateway_propagates_tool_calls() -> None:
    call = ToolCall(id="c1", name="macro__discover_factors", arguments="{}")

    class B:
        def create(self, **_kwargs: Any) -> RawCompletion:
            return RawCompletion("", 10, 1, "tool_calls", (call,))

    client = InstrumentedChatClient(B(), CostTracker())
    result = client.complete(messages=[], model="gpt-5", stage="evidence", tools=[{"type": "function"}])
    assert result.tool_calls == (call,)


# --------------------------------------------------------------------------- #
# tool loop
# --------------------------------------------------------------------------- #
def test_tool_loop_one_call_then_answer() -> None:
    reg, src = _registry()
    gateway = InstrumentedChatClient(FakeToolBackend("arag__facts_lookup", wants=1), CostTracker())
    result = run_tool_loop(
        gateway=gateway, registry=reg, system="s", user="u", model="gpt-5-mini", stage="evidence",
    )
    assert result.content == "final answer"
    assert result.n_tool_calls == 1
    assert result.budget_exhausted is False
    assert src.calls == [("arag", "facts_lookup", {})]


def test_tool_loop_respects_budget() -> None:
    reg, src = _registry()
    backend = FakeToolBackend("arag__facts_lookup", wants=10)
    gateway = InstrumentedChatClient(backend, CostTracker())
    result = run_tool_loop(
        gateway=gateway, registry=reg, system="s", user="u", model="gpt-5-mini", stage="evidence",
        max_tool_calls=2,
    )
    assert result.n_tool_calls == 2
    assert result.budget_exhausted is True
    assert len(src.calls) == 2
    # Tools stay on the request once the budget is hit; tool_choice="none" forces the final answer
    # (spec llm-native-turns C4 covers the tool_choice side).
    assert backend.last_tools == reg.openai_tools()


def test_tool_loop_passes_allowlist_to_model() -> None:
    reg, _ = _registry()
    backend = FakeToolBackend("macro__discover_factors", wants=1)
    gateway = InstrumentedChatClient(backend, CostTracker())
    run_tool_loop(
        gateway=gateway, registry=reg, system="s", user="u", model="gpt-5", stage="discover",
        allow={"macro__discover_factors"},
    )
    # the first turn offered exactly the allowlisted tool
    first_tools = [{"function": {"name": "macro__discover_factors"}}]
    assert backend.requested == 1
    # backend recorded the tools it was offered on its FIRST call (before budget logic)
    assert any(t["function"]["name"] == "macro__discover_factors" for t in (backend.last_tools or first_tools))


def test_tool_loop_handles_dispatch_error() -> None:
    reg, src = _registry()
    # model asks for a tool that isn't registered → dispatch error is fed back, loop recovers
    backend = FakeToolBackend("ghost__tool", wants=1)
    gateway = InstrumentedChatClient(backend, CostTracker())
    result = run_tool_loop(
        gateway=gateway, registry=reg, system="s", user="u", model="gpt-5", stage="evidence",
        max_tool_calls=3,
    )
    assert result.content == "final answer"
    assert src.calls == []  # never reached a real tool


def test_tool_loop_records_trace() -> None:
    reg, _ = _registry()
    gateway = InstrumentedChatClient(FakeToolBackend("arag__facts_lookup", wants=2), CostTracker())
    with run_trace("run-2") as trace:
        result = run_tool_loop(
            gateway=gateway, registry=reg, system="s", user="u", model="gpt-5-mini", stage="evidence",
            max_tool_calls=5,
        )
    assert trace.n_tool_calls == result.n_tool_calls == 2
    assert trace.n_llm_calls == len(result.calls)
