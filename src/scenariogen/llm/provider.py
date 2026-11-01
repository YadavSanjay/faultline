"""LLM scenario provider (P2).

The bridge from a calibrated LLM scenario to the *existing* scenario machinery — mirrors the
role of `MacroScenarioProvider`, but where Macro sources driver legs from MacroRAG, this turns
an LLM-produced `CalibratedScenario` into a `ScenarioDefinition`. From there the unchanged
pipeline (covariance propagation → full-reval pricing → plausibility → versioning) takes over.

The LLM emits absolute, calibrated magnitudes, so `severity_scale` is 1.0 (the severity is baked
into the shocks, not a tier multiplier).
"""

from __future__ import annotations

import re

from ..scenario_gen import ScenarioDefinition
from .schema import CalibratedScenario

_LLM_SEVERITY = "llm"


def slugify(name: str) -> str:
    """A filesystem/id-safe slug for a scenario name (for narrative filenames, etc.)."""
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip().lower()).strip("_")
    return slug or "scenario"


class LLMScenarioProvider:
    """Converts a `CalibratedScenario` into a governed `ScenarioDefinition`."""

    def to_definition(self, calibrated: CalibratedScenario, *, severity: str = _LLM_SEVERITY) -> ScenarioDefinition:
        if not calibrated.legs:
            raise ValueError(f"calibrated scenario {calibrated.name!r} has no driver legs")
        return ScenarioDefinition.from_config(
            calibrated.name,
            calibrated.to_definition_spec(),
            severity=severity,
            severity_scale=1.0,
        )
