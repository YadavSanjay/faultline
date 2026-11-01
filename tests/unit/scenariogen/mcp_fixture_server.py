"""A tiny stdio MCP server for the McpClient timeout-policy unit tests.

``slow`` blocks for ``seconds`` in a worker thread (as MacroRAG's tools do); ``fast`` answers at once.
Imports only ``mcp``, ``anyio`` and the standard library.
"""

from __future__ import annotations

import time

import anyio
from mcp.server.fastmcp import FastMCP

server = FastMCP("mcp-timeout-policy-unit-fixture")


@server.tool()
async def slow(seconds: float = 2.0) -> str:
    """Sleeps ``seconds`` in a worker thread, then answers."""
    await anyio.to_thread.run_sync(time.sleep, seconds)
    return '{"tool": "slow", "ok": true}'


@server.tool()
async def fast() -> str:
    """Answers at once."""
    return '{"tool": "fast", "ok": true}'


if __name__ == "__main__":
    server.run(transport="stdio")
