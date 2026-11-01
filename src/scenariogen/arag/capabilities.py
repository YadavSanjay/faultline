"""Single source of truth for ARag's capability surface (harness-audit F3).

ARag is reached **two ways, by design**, because it serves two different consumers:

  * the DETERMINISTIC dual-RAG provider calls fixed REST endpoints (``arag/client.py``);
  * the AGENTIC LLM flow lets the model pick ARag MCP tools (``llm/mcp`` + ``mcp.json``).

They cannot share a transport — one is a fixed sequence of REST calls, the other is
model-driven tool-calling over a stdio subprocess owned by the ARag repo — but they MUST
agree on *what ARag can do*. This module is the one catalog both surfaces reference, so
capability drift (a tool present on one surface but missing/renamed on the other) is caught by
a test (``tests/scenariogen/test_arag_capabilities.py``) instead of discovered in production.

The REST client imports the ``AragPaths`` constants below (so the endpoints live in exactly
one place), and the drift test asserts the ARag tools wired into ``mcp.json``'s phase
allowlists are exactly the ``mcp_tool`` entries here. Divergence is explicit:

  * ``facts_compute`` is **MCP-only** — server-side fact arithmetic the model may call; the
    deterministic path composes ratios itself (``calibrate.py`` share = numerator/denominator).
  * ``health`` / ``answer`` are **REST-only** — the preflight liveness probe and the full
    synthesized-prose pipeline, neither of which the agentic tool loop uses.
"""

from __future__ import annotations

from dataclasses import dataclass


class AragPaths:
    """The REST endpoints the deterministic ``AragClient`` calls — the one place they're defined."""

    HEALTH = "/api/health"
    COVERAGE = "/api/v1/coverage"
    NARRATIVE_SEARCH = "/api/v1/narrative/search"
    FACTS_LOOKUP = "/api/v1/facts/lookup"
    ANSWER = "/api/v1/answer"


@dataclass(frozen=True)
class AragCapability:
    name: str                 # canonical capability, e.g. "facts_lookup"
    rest_path: str | None     # REST endpoint the deterministic client exposes, else None
    mcp_tool: str | None      # MCP tool name the agentic flow exposes, else None
    purpose: str


ARAG_CAPABILITIES: tuple[AragCapability, ...] = (
    AragCapability("health", AragPaths.HEALTH, None,
                   "liveness/readiness (REST preflight only)"),
    AragCapability("coverage", AragPaths.COVERAGE, "coverage",
                   "ingested filers / fiscal-years / forms"),
    AragCapability("narrative_search", AragPaths.NARRATIVE_SEARCH, "narrative_search",
                   "qualitative Risk-Factor / MD&A chunks"),
    AragCapability("facts_lookup", AragPaths.FACTS_LOOKUP, "facts_lookup",
                   "deterministic cited fact series"),
    AragCapability("facts_compute", None, "facts_compute",
                   "server-side fact arithmetic (agentic only; deterministic path composes "
                   "ratios itself in calibrate.py)"),
    AragCapability("answer", AragPaths.ANSWER, None,
                   "full synthesized prose pipeline (REST; narrative grounding only)"),
)

# The endpoints/tools each surface is expected to expose — asserted against reality by the test.
REST_PATHS: frozenset[str] = frozenset(c.rest_path for c in ARAG_CAPABILITIES if c.rest_path)
MCP_TOOLS: frozenset[str] = frozenset(c.mcp_tool for c in ARAG_CAPABILITIES if c.mcp_tool)
