"""Fixtures for the mcp-call-timeouts spec tests.

C4 talks to a real stdio MCP server: ``fixture_mcp_server.py`` in this directory, started by ``McpClient``
with the running interpreter (``sys.executable``) and the parent environment (spec Notes). No network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from src.scenariogen.llm.mcp.client import ServerSpec

FIXTURE_SERVER = Path(__file__).resolve().parent / "fixture_mcp_server.py"


@pytest.fixture
def fixture_specs() -> dict[str, ServerSpec]:
    """One server, ``fx``, running the committed fixture script."""
    return {"fx": ServerSpec(name="fx", command=sys.executable, args=[str(FIXTURE_SERVER)], tool_prefix="fx")}
