"""Evidence phase (P2).

A bounded agentic loop: the model uses ARag (facts/narrative) and MacroRAG (query/citations)
tools to gather grounded, cited risk-factor evidence for a scope (a theme in Mode 2, a company
in Mode 1). It returns a structured bundle — best-effort parsed company/macro risk factors plus
a prose summary that grounds the downstream propose/calibrate phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..config import LLMSettings
from ..gateway import InstrumentedChatClient, LLMCallResult
from ..mcp.loop import run_tool_loop
from ..mcp.registry import McpToolRegistry
from ..prompts import PromptManager
from ..schema import CompanyRiskFactor, MacroRiskFactor
from .parsing import extract_json_object


@dataclass
class EvidenceBundle:
    scope: str
    text: str
    company_factors: list[CompanyRiskFactor] = field(default_factory=list)
    macro_factors: list[MacroRiskFactor] = field(default_factory=list)
    calls: list[LLMCallResult] = field(default_factory=list)


def _coerce[T: BaseModel](model: type[T], items: Any) -> list[T]:
    out: list[T] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            out.append(model.model_validate(item))
        except Exception:  # lenient: skip malformed rows rather than fail the run
            continue
    return out


def _parse(content: str) -> tuple[list[CompanyRiskFactor], list[MacroRiskFactor], str]:
    try:
        obj = extract_json_object(content)
    except ValueError:
        return [], [], content
    company = _coerce(CompanyRiskFactor, obj.get("company_factors"))
    macro = _coerce(MacroRiskFactor, obj.get("macro_factors"))
    summary = str(obj.get("summary") or content)
    return company, macro, summary


def gather_evidence(
    *,
    gateway: InstrumentedChatClient,
    registry: McpToolRegistry,
    prompts: PromptManager,
    settings: LLMSettings,
    allow: set[str],
    scope: str,
    focus: str,
) -> EvidenceBundle:
    system = prompts.render("evidence_system")
    user = prompts.render("evidence_user", scope=scope, focus=focus)
    result = run_tool_loop(
        gateway=gateway,
        registry=registry,
        system=system,
        user=user,
        model=settings.model_for("evidence"),
        stage="evidence",
        allow=allow,
        max_tool_calls=settings.budgets.max_tool_calls_per_phase,
        max_completion_tokens=settings.max_tokens_for("evidence"),
    )
    company, macro, text = _parse(result.content)
    return EvidenceBundle(scope=scope, text=text, company_factors=company, macro_factors=macro, calls=result.calls)
