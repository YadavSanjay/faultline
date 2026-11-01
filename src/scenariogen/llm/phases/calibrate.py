"""Calibrate phase (P2).

The flow first calls ``macro_synthesize_shock_vector`` itself (`synthesize_macro_shocks`), so
every calibrated scenario rests on one code-made MacroRAG synthesis rather than on whether the model
chose to call it. A bounded agentic loop then turns that synthesis (signed, unit-typed, cited,
mutually correlated macro shocks) into the macro driver legs and grounds the per-name equity legs in
the evidence, emitting a `CalibratedScenario` as JSON. The macro shock schema is pre-fetched and
injected so the model has the valid field/unit catalog without spending a tool call on it. The
synthesis's ``world_view_id`` is withheld from the model: the flow sets it from the response.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ...factor_keys import canonical_fx_key
from ..config import LLMSettings
from ..gateway import InstrumentedChatClient
from ..mcp.loop import run_tool_loop
from ..mcp.registry import McpToolRegistry
from ..prompts import PromptManager
from ..schema import CalibratedScenario, ScenarioProposal
from .parsing import extract_json_object

logger = logging.getLogger("scenariogen.llm.phases.calibrate")

# MacroRAG's tool is named `macro_shock_schema`; server-prefixed -> `macro__macro_shock_schema`.
_SHOCK_SCHEMA_TOOL = "macro__macro_shock_schema"
SYNTHESIZE_TOOL = "macro__macro_synthesize_shock_vector"
# Response keys the model never sees: the flow owns the world-view id (spec llm-grounding-gate).
_WITHHELD_SYNTHESIS_KEYS = frozenset({"world_view_id"})


def fetch_shock_schema(registry: McpToolRegistry) -> str:
    """Best-effort pre-fetch of the macro shock-field catalog (empty string if unavailable)."""
    if _SHOCK_SCHEMA_TOOL not in registry.names():
        return ""
    try:
        return registry.dispatch(_SHOCK_SCHEMA_TOOL, {})
    except Exception as exc:
        logger.warning("could not pre-fetch %s: %s", _SHOCK_SCHEMA_TOOL, exc)
        return ""


def synthesize_macro_shocks(registry: McpToolRegistry, proposal: ScenarioProposal) -> dict[str, Any]:
    """Calls MacroRAG's shock-vector synthesis once for a proposal, from code.

    Args:
        registry: The MCP tool registry.
        proposal: The scenario proposal; its name, narrative and trigger form the question.

    Returns:
        The parsed synthesis response.

    Raises:
        ValueError: The response is not a JSON object.
        Exception: Whatever the tool call raised (e.g. a timeout); the caller refuses the scenario.
    """
    question = (f"Synthesize the correlated macro shock vector for the stress scenario "
                f"'{proposal.name}': {proposal.narrative} Trigger: {proposal.trigger}".strip())
    arguments: dict[str, Any] = {"question": question, "horizon_days": proposal.horizon_days}
    if proposal.narrative:
        arguments["scenario"] = proposal.narrative
    raw = registry.dispatch(SYNTHESIZE_TOOL, arguments)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{SYNTHESIZE_TOOL} returned non-JSON output: {raw[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{SYNTHESIZE_TOOL} returned {type(parsed).__name__}, not a JSON object")
    return parsed


def synthesis_for_model(synthesis: dict[str, Any] | None) -> str:
    """The synthesis as prompt text, without the keys the flow owns (``world_view_id``)."""
    if not synthesis:
        return "(no MacroRAG synthesis available)"
    visible = {k: v for k, v in synthesis.items() if k not in _WITHHELD_SYNTHESIS_KEYS}
    return json.dumps(visible, indent=2, default=str)


def calibrate_scenario(
    *,
    gateway: InstrumentedChatClient,
    registry: McpToolRegistry,
    prompts: PromptManager,
    settings: LLMSettings,
    proposal: ScenarioProposal,
    evidence: str,
    allow: set[str],
    shock_schema: str | None = None,
    macro_synthesis: dict[str, Any] | None = None,
) -> CalibratedScenario:
    schema_text = shock_schema if shock_schema is not None else fetch_shock_schema(registry)
    schema_text = schema_text or "(call macro_shock_schema for the field catalog)"
    system = prompts.render("calibrate_system", shock_schema=schema_text)
    user = prompts.render("calibrate_user", proposal=proposal.model_dump_json(indent=2), evidence=evidence,
                          macro_synthesis=synthesis_for_model(macro_synthesis))
    result = run_tool_loop(
        gateway=gateway,
        registry=registry,
        system=system,
        user=user,
        model=settings.model_for("calibrate"),
        stage="calibrate",
        allow=allow,
        max_tool_calls=settings.budgets.max_tool_calls_per_phase,
        max_completion_tokens=settings.max_tokens_for("calibrate"),
    )
    obj = extract_json_object(result.content)
    calibrated = CalibratedScenario.model_validate(obj)
    calibrated = _canonicalize_factors(calibrated)
    if not calibrated.name:
        calibrated = calibrated.model_copy(update={"name": proposal.name})
    if not calibrated.legs:
        raise ValueError(f"calibration produced no driver legs for {proposal.name!r}")
    return calibrated


def _canonicalize_factors(calibrated: CalibratedScenario) -> CalibratedScenario:
    """Normalize FX factor keys to the canonical ``fx:<CCY>`` form the engine/taxonomy expect.

    The model tends to echo MacroRAG's USD-base pair names (``fx:USDTWD``); canonicalizing here
    means the stored artifact, narrative, provenance, and pricing all speak the same factor keys.
    """
    legs = [leg.model_copy(update={"factor": canonical_fx_key(leg.factor)}) for leg in calibrated.legs]
    overlays = {canonical_fx_key(k): v for k, v in calibrated.overlays.items()}
    return calibrated.model_copy(update={"legs": legs, "overlays": overlays})
