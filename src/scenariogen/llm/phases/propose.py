"""Propose phase (P2/P3).

A single structured (JSON) call — no tools. Aggregates the gathered evidence into portfolio-level
stress scenarios. Mode 2 asks for one scenario per theme (`propose_scenario`); Mode 1 asks for up
to N candidate scenarios (`propose_scenarios`) which are then priced and ranked by max loss.
"""

from __future__ import annotations

from ..config import LLMSettings
from ..gateway import InstrumentedChatClient
from ..prompts import PromptManager
from ..schema import ScenarioProposal
from .json_schemas import PROPOSE_SCHEMA, response_format_for
from .parsing import extract_json_object


def _proposals_from(obj: dict[str, object]) -> list[ScenarioProposal]:
    scenarios = obj.get("scenarios")
    if isinstance(scenarios, list) and scenarios:
        return [ScenarioProposal.model_validate(s) for s in scenarios if isinstance(s, dict)]
    if "name" in obj:  # model returned a single scenario object directly
        return [ScenarioProposal.model_validate(obj)]
    return []


def propose_scenarios(
    *,
    gateway: InstrumentedChatClient,
    prompts: PromptManager,
    settings: LLMSettings,
    intent: str,
    portfolio_summary: str,
    evidence: str,
    max_scenarios: int,
) -> list[ScenarioProposal]:
    """Propose up to ``max_scenarios`` candidate portfolio-level scenarios (Mode 1)."""
    system = prompts.render("propose_system", max_scenarios=max_scenarios)
    user = prompts.render("propose_user", intent=intent, portfolio_summary=portfolio_summary, evidence=evidence)
    result = gateway.complete(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=settings.model_for("propose"),
        stage="propose",
        max_completion_tokens=settings.max_tokens_for("propose"),
        response_format=response_format_for(settings, "propose", "scenario_proposals", PROPOSE_SCHEMA),
    )
    proposals = _proposals_from(extract_json_object(result.content))
    if not proposals:
        raise ValueError("propose phase returned no scenarios")
    return proposals[:max_scenarios]


def propose_scenario(
    *,
    gateway: InstrumentedChatClient,
    prompts: PromptManager,
    settings: LLMSettings,
    intent: str,
    portfolio_summary: str,
    evidence: str,
    max_scenarios: int = 1,
) -> ScenarioProposal:
    """Propose a single scenario (Mode 2 — one per theme)."""
    return propose_scenarios(
        gateway=gateway, prompts=prompts, settings=settings, intent=intent,
        portfolio_summary=portfolio_summary, evidence=evidence, max_scenarios=max_scenarios,
    )[0]
