"""Deterministic grounding gate for LLM-calibrated scenarios (spec `llm-grounding-gate`).

Runs after verify and fact-check, before pricing: a scenario whose legs are all marked UNSUPPORTED,
whose MacroRAG synthesis has a coverage gap or no world-view id, or whose legs are uncited or cite
chunk ids no MCP tool ever returned, is refused rather than priced and published. The checks are
pure functions over the calibrated scenario, the scenario's synthesis response and the set of ids
the run's tool outputs carried, so the verdict never depends on the model.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from .schema import CalibratedScenario

# Keys whose string value is a citable id, and keys whose list of strings are. Only chunk and fact ids
# count: ARag uses a generic `id` for plan-step ids ("api_screen"), which are not evidence.
_ID_KEYS = frozenset({"chunk_id", "fact_id"})
_ID_LIST_KEYS = frozenset({"chunk_ids", "fact_ids"})

ALL_LEGS_UNSUPPORTED = "all_legs_unsupported"
COVERAGE_GAP = "coverage_gap"
MISSING_WORLD_VIEW_ID = "missing_world_view_id"
UNCITED_LEG = "uncited_leg"
UNSEEN_CITATION = "unseen_citation"


class GroundingVerdict(NamedTuple):
    """The gate's outcome: ``ok`` iff ``reasons`` is empty."""

    ok: bool
    reasons: tuple[str, ...]


def collect_chunk_ids(tool_output: str) -> frozenset[str]:
    """Every chunk/fact id in one MCP tool output.

    Args:
        tool_output: The raw text an MCP tool returned.

    Returns:
        Every string under a ``chunk_id``/``fact_id`` key and every string element of a
        ``chunk_ids``/``fact_ids`` list, at any depth; empty if the output is not valid JSON.
    """
    try:
        parsed = json.loads(tool_output)
    except (json.JSONDecodeError, TypeError):
        return frozenset()
    ids: set[str] = set()
    stack: list[Any] = [parsed]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in _ID_KEYS and isinstance(value, str):
                    ids.add(value)
                elif key in _ID_LIST_KEYS and isinstance(value, list):
                    ids.update(v for v in value if isinstance(v, str))
                else:
                    stack.append(value)
    return frozenset(ids)


def world_view_id_of(synthesis: dict[str, Any]) -> str:
    """The synthesis response's ``world_view_id``, or ``""`` when absent or not a non-empty string."""
    value = synthesis.get("world_view_id")
    return value if isinstance(value, str) else ""


def assess_grounding(
    calibrated: CalibratedScenario, *, synthesis: dict[str, Any], seen_ids: frozenset[str]
) -> GroundingVerdict:
    """Apply the grounding rule table to one calibrated scenario.

    Args:
        calibrated: The scenario after verify and fact-check.
        synthesis: The parsed MacroRAG synthesize response for this scenario.
        seen_ids: Every chunk id returned by an MCP tool so far in the run.

    Returns:
        The verdict; its reasons are, in this order, whichever of ``all_legs_unsupported``,
        ``coverage_gap``, ``missing_world_view_id``, ``uncited_leg`` and ``unseen_citation`` hold.
    """
    reasons: list[str] = []
    legs = calibrated.legs
    if legs and _all_unsupported(calibrated):
        reasons.append(ALL_LEGS_UNSUPPORTED)
    diagnostics = synthesis.get("synthesis_diagnostics")
    flagged_gap = isinstance(diagnostics, dict) and diagnostics.get("coverage_gap") is True
    vector = synthesis.get("shock_vector")
    if flagged_gap or not (isinstance(vector, dict) and vector):
        reasons.append(COVERAGE_GAP)
    if not world_view_id_of(synthesis):
        reasons.append(MISSING_WORLD_VIEW_ID)
    if any(not leg.citations for leg in legs):
        reasons.append(UNCITED_LEG)
    if any(c.chunk_id not in seen_ids for leg in legs for c in leg.citations):
        reasons.append(UNSEEN_CITATION)
    return GroundingVerdict(ok=not reasons, reasons=tuple(reasons))


def _all_unsupported(calibrated: CalibratedScenario) -> bool:
    """True when verify recorded an UNSUPPORTED verdict for every leg's factor."""
    verdicts = (calibrated.verification or {}).get("verdicts") or []
    unsupported = {
        str(v.get("factor")) for v in verdicts
        if isinstance(v, dict) and str(v.get("verdict", "")).upper() == "UNSUPPORTED"
    }
    return all(leg.factor in unsupported for leg in calibrated.legs)
