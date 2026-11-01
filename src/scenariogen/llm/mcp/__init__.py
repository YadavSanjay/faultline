"""MCP client layer for the scenariogen LLM flow (P1).

Consumes the ARag (`arag-analyst`) and MacroRAG (`macro-rag`) MCP servers over stdio:
``McpClient`` (sync facade over async sessions), ``McpToolRegistry`` (namespaced MCP→OpenAI
tool conversion + dispatch), and ``run_tool_loop`` (bounded agentic function-calling loop).
"""

from __future__ import annotations

from .client import McpClient, ServerSpec, ToolListing, parse_server_specs
from .loop import ToolLoopResult, run_tool_loop
from .registry import McpToolRegistry, RegisteredTool, ToolSource

__all__ = [
    "McpClient",
    "McpToolRegistry",
    "RegisteredTool",
    "ServerSpec",
    "ToolListing",
    "ToolLoopResult",
    "ToolSource",
    "parse_server_specs",
    "run_tool_loop",
]
