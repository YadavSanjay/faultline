"""MCP tool registry (P1).

Discovers the tools each connected server advertises, namespaces them (``arag__facts_lookup``,
``macro__synthesize_shock_vector``) to avoid collisions, converts their MCP JSON Schemas into
OpenAI function-tool definitions, and dispatches namespaced tool calls back to the right server.
Per-phase allowlists keep each agentic loop focused on the tools that phase should use.

The registry depends only on a `ToolSource` (anything exposing `list_tools` / `call_tool`), so
it is fully testable with a fake source — no servers spawned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..grounding import collect_chunk_ids
from ..trace import current_run
from .client import ServerSpec, ToolListing

_NS_SEP = "__"


class ToolSource(Protocol):
    """The minimal surface the registry needs (satisfied by `McpClient` and test fakes)."""

    def list_tools(self, server: str) -> list[ToolListing]: ...
    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class RegisteredTool:
    namespaced_name: str
    server: str
    tool: str
    description: str
    input_schema: dict[str, Any]


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Coerce an MCP inputSchema into a valid OpenAI function `parameters` object schema."""
    if not schema:
        return {"type": "object", "properties": {}}
    out = dict(schema)
    out.setdefault("type", "object")
    if out["type"] == "object":
        out.setdefault("properties", {})
    return out


class McpToolRegistry:
    """Namespaced tool catalog over one or more MCP servers."""

    def __init__(self, source: ToolSource, specs: dict[str, ServerSpec]):
        self._source = source
        self._tools: dict[str, RegisteredTool] = {}
        self._seen_ids: set[str] = set()  # every chunk/fact id a tool output carried (grounding gate)
        for name, spec in specs.items():
            for listing in source.list_tools(name):
                namespaced = f"{spec.prefix}{_NS_SEP}{listing.name}"
                self._tools[namespaced] = RegisteredTool(
                    namespaced_name=namespaced,
                    server=name,
                    tool=listing.name,
                    description=listing.description,
                    input_schema=listing.input_schema,
                )

    def names(self) -> list[str]:
        return sorted(self._tools)

    def openai_tools(self, allow: set[str] | None = None) -> list[dict[str, Any]]:
        """OpenAI function-tool definitions, optionally filtered to an allowlist."""
        tools: list[dict[str, Any]] = []
        for name in sorted(self._tools):
            if allow is not None and name not in allow:
                continue
            registered = self._tools[name]
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": registered.namespaced_name,
                        "description": registered.description,
                        "parameters": _sanitize_schema(registered.input_schema),
                    },
                }
            )
        return tools

    def dispatch(self, namespaced_name: str, arguments: dict[str, Any]) -> str:
        """Route a namespaced tool call to its server; records the call into the active trace."""
        registered = self._tools.get(namespaced_name)
        if registered is None:
            raise KeyError(f"unknown tool {namespaced_name!r} (have {self.names()})")
        run = current_run()
        if run is not None:
            run.record_tool_call(namespaced_name)
        output = self._source.call_tool(registered.server, registered.tool, arguments)
        self._seen_ids |= collect_chunk_ids(output)
        return output

    def seen_ids(self) -> frozenset[str]:
        """Every chunk/fact id returned by a tool dispatched through this registry so far."""
        return frozenset(self._seen_ids)
