"""ARag capability catalog is the single source of truth for both surfaces (harness-audit F3).

Two consumers reach ARag by design — the deterministic REST client and the agentic MCP tool
loop — but they must agree on ARag's capability surface. These tests fail on drift:
  * the REST paths the AragClient actually calls == the catalog's rest_path set;
  * the ARag tools wired into mcp.json's phase allowlists == the catalog's mcp_tool set.
So adding/renaming a tool on one surface without updating the catalog (and the other) is caught
here rather than in production.
"""

from __future__ import annotations

import json
from typing import Any

from src.scenariogen.arag.capabilities import MCP_TOOLS, REST_PATHS
from src.scenariogen.arag.client import AragClient
from src.scenariogen.config import config_dir


class _RecordingTransport:
    """Captures the path of every REST call and returns a benign payload for parsing."""

    def __init__(self) -> None:
        self.paths: set[str] = set()

    def get(self, path: str) -> dict[str, Any]:
        self.paths.add(path)
        return {"status": "ok", "pipelines": True, "items": []}

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.paths.add(path)
        return {"company": {"ticker": "NVDA"}, "chunks": [], "series": []}


def test_rest_client_hits_exactly_the_catalog_paths():
    t = _RecordingTransport()
    client = AragClient(t)
    # Exercise every REST capability the deterministic path uses.
    client.health()
    client.coverage()
    client.narrative_search("NVDA", "risk")
    client.facts_lookup("NVDA", "Revenue", [2025])
    client.answer("summarize NVDA risk factors")
    assert t.paths == set(REST_PATHS), (
        f"AragClient paths {t.paths} drifted from the capability catalog {set(REST_PATHS)}")


def test_mcp_allowlists_reference_exactly_the_catalog_tools():
    mcp = json.loads((config_dir() / "llm" / "mcp.json").read_text(encoding="utf-8"))
    prefix = mcp["servers"]["arag"]["tool_prefix"] + "__"      # "arag__"
    used: set[str] = set()
    for tools in mcp.get("phase_allowlists", {}).values():
        for tool in tools:
            if tool.startswith(prefix):
                used.add(tool[len(prefix):])
    assert used == set(MCP_TOOLS), (
        f"mcp.json ARag tools {used} drifted from the capability catalog {set(MCP_TOOLS)}")
