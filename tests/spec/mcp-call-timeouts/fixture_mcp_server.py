"""A tiny stdio MCP server for the mcp-call-timeouts C4 spec tests (docs/specs/mcp-call-timeouts.md, Notes).

Two tools:

- ``slow`` blocks for ``SLOW_SECONDS`` in a worker thread (``anyio.to_thread.run_sync``), as MacroRAG's
  tools do, so the server's event loop stays free to answer ``fast`` while an abandoned ``slow`` call is
  still running, and client-side cancellation cannot stop it.
- ``fast`` returns the fixed JSON string ``FAST_RESULT`` at once.

Imports only ``mcp`` (pinned 1.12.4), ``anyio`` and the standard library. Run as
``<python> fixture_mcp_server.py``; the transport is stdio.
"""

from __future__ import annotations

import time

import anyio
from mcp.server.fastmcp import FastMCP

SLOW_SECONDS = 8.0
FAST_RESULT = '{"tool": "fast", "ok": true, "value": 42}'

server = FastMCP("mcp-call-timeouts-fixture")


@server.tool()
async def slow() -> str:
    """Sleeps SLOW_SECONDS in a worker thread, then answers."""
    await anyio.to_thread.run_sync(time.sleep, SLOW_SECONDS)
    return '{"tool": "slow", "ok": true}'


@server.tool()
async def fast() -> str:
    """Answers at once with a fixed JSON string."""
    return FAST_RESULT


if __name__ == "__main__":
    server.run(transport="stdio")
