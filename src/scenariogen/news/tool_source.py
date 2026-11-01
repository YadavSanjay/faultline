"""News ``ToolSource`` adapter for the agentic ``llm/`` path (A4, market-intelligence P3).

`llm/mcp/registry.py`'s ``ToolSource`` is a Protocol (``list_tools`` / ``call_tool``), not MCP-bound,
so a local CSV-backed source registers as an OpenAI tool without running an MCP server (design §2.4).
This adapter gives the agentic flow the *same* 8-K + salience events the deterministic dual-RAG path
gets — otherwise only the deterministic path would see news (§9). Small, but **not optional**: it is
the one manual integration point the single-project placement costs, and the reason A4 is flagged
not-optional.

One tool, ``news_events``: given a ticker (and the run's PIT ``as_of``), returns that name's
allowlisted 8-K events (item codes, dates, accession numbers, weights), its GDELT tone z-score, and
the resolved clamped multiplier — as a JSON string the model can cite. Read-only; no side effects.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from ..llm.mcp.client import ToolListing
from .provider import NewsEventProvider, NewsResolution

_SERVER = "news"
_TOOL = "news_events"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string", "description": "Portfolio underlier, e.g. NVDA (upper-case)."},
    },
    "required": ["ticker"],
}


class NewsToolSource:
    """A local, CSV-backed ``ToolSource`` exposing the news event stream to the agentic loop."""

    def __init__(self, provider: NewsEventProvider, as_of: date):
        self._provider = provider
        self._as_of = as_of
        self._resolution: NewsResolution | None = None

    @property
    def resolution(self) -> NewsResolution:
        if self._resolution is None:
            self._resolution = self._provider.resolve(self._as_of)
        return self._resolution

    def list_tools(self, server: str) -> list[ToolListing]:
        if server != _SERVER:
            return []
        return [ToolListing(
            name=_TOOL,
            description=(
                "SEC Form 8-K events (item codes, dates, accession numbers, weights) and GDELT tone "
                "for a portfolio underlier, point-in-time to the run's valuation date, plus the "
                "resolved clamped news multiplier on that name's idiosyncratic shock. Structured "
                "metadata only — cite events by accession number."
            ),
            input_schema=_SCHEMA,
        )]

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        if server != _SERVER or tool != _TOOL:
            raise KeyError(f"unknown news tool {server}.{tool}")
        ticker = str(arguments.get("ticker", "")).upper()
        res = self.resolution
        leg = res.leg_for(ticker)
        payload = {
            "ticker": ticker,
            "as_of": res.as_of,
            "gate_applied": res.applied,
            "multiplier": res.multiplier_for(ticker),
            "news": leg.to_dict() if leg else None,
            "live_triggers": {item: names for item, names in res.live_triggers.items() if ticker in names},
            "earnings_dates": res.earnings.get(ticker, []),
        }
        return json.dumps(payload, default=str)
