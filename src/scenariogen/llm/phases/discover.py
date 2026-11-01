"""Discover phase (P3, Mode 1).

The comprehensive flow's opening move: a bounded agentic loop that enumerates the ranked risk-factor
universe the portfolio is exposed to — top-down macro factors (MacroRAG `discover_factors`/`vocabulary`)
and bottom-up company themes (ARag `coverage`/`narrative_search`). It returns a prose summary of the
ranked factor themes (which grounds the evidence + propose phases) plus best-effort parsed factors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import LLMSettings
from ..gateway import InstrumentedChatClient, LLMCallResult
from ..mcp.loop import run_tool_loop
from ..mcp.registry import McpToolRegistry
from ..prompts import PromptManager
from ..schema import CompanyRiskFactor, MacroRiskFactor
from .evidence import _coerce  # lenient list coercion, shared with the evidence phase
from .parsing import extract_json_object


@dataclass
class DiscoveryResult:
    text: str
    macro_factors: list[MacroRiskFactor] = field(default_factory=list)
    company_factors: list[CompanyRiskFactor] = field(default_factory=list)
    calls: list[LLMCallResult] = field(default_factory=list)


def _parse(content: str) -> tuple[list[MacroRiskFactor], list[CompanyRiskFactor], str]:
    try:
        obj = extract_json_object(content)
    except ValueError:
        return [], [], content
    macro = _coerce(MacroRiskFactor, obj.get("macro_factors"))
    company = _coerce(CompanyRiskFactor, obj.get("company_factors"))
    summary = str(obj.get("summary") or content)
    return macro, company, summary


def discover_factor_universe(
    *,
    gateway: InstrumentedChatClient,
    registry: McpToolRegistry,
    prompts: PromptManager,
    settings: LLMSettings,
    allow: set[str],
    portfolio_summary: str,
    tickers: str,
    asset_classes: str,
) -> DiscoveryResult:
    system = prompts.render("discover_system")
    user = prompts.render(
        "discover_user", portfolio_summary=portfolio_summary, tickers=tickers, asset_classes=asset_classes
    )
    result = run_tool_loop(
        gateway=gateway,
        registry=registry,
        system=system,
        user=user,
        model=settings.model_for("discover"),
        stage="discover",
        allow=allow,
        max_tool_calls=settings.budgets.max_tool_calls_per_phase,
        max_completion_tokens=settings.max_tokens_for("discover"),
    )
    macro, company, text = _parse(result.content)
    return DiscoveryResult(text=text, macro_factors=macro, company_factors=company, calls=result.calls)
