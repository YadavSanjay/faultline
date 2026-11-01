"""Structured LLM I/O models (P0).

pydantic v2 models for the data that crosses the LLM boundary — evidence the agent gathers,
the scenarios it proposes, and the calibrated driver shocks it emits. Using pydantic (as ARag
does, and as the pricer's P5 config validation does) means LLM JSON is validated at the
boundary and the rest of the flow works with typed objects.

The key bridge is `CalibratedScenario.to_definition_spec()` → a plain spec dict that
`src.scenariogen.scenario_gen.ScenarioDefinition.from_config` already accepts, so a calibrated
LLM scenario flows into the *existing* materialize → price → gate → version machinery unchanged
(driver/overlay magnitudes are emitted as canonical native-unit floats, which `to_canonical`
passes through as-is).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Method strings mirror src/scenariogen/scenario_gen.py (conditional_covariance | drivers_only).
METHOD_CONDITIONAL = "conditional_covariance"


class Citation(BaseModel):
    """A pointer back to a source chunk (ARag fact/narrative or MacroRAG corpus chunk)."""

    chunk_id: str
    source: str = ""  # "arag" | "macro"
    note: str = ""


class CompanyRiskFactor(BaseModel):
    """A risk factor a portfolio company declares (10-K/Q), with grounding."""

    ticker: str
    factor: str
    description: str = ""
    magnitude: float | None = None
    unit: str = ""
    citations: list[Citation] = Field(default_factory=list)


class MacroRiskFactor(BaseModel):
    """A macro risk factor relevant to the portfolio (from MacroRAG discovery/query)."""

    factor_tag: str
    relevance: str = ""  # high | medium | low
    rationale: str = ""
    citations: list[Citation] = Field(default_factory=list)


class DriverLeg(BaseModel):
    """One calibrated driver shock: a canonical factor key → native-unit move, with provenance."""

    factor: str  # canonical factor_keys key, e.g. "eq:NVDA", "ir:DGS10", "credit"
    move: float  # canonical native units (decimal): IR/credit additive, equity/FX relative
    origin: str = "driver"  # macro | equity | overlay | driver
    rationale: str = ""
    citations: list[Citation] = Field(default_factory=list)


class ScenarioProposal(BaseModel):
    """A portfolio-level stress scenario the LLM proposes (pre-calibration)."""

    name: str
    theme: str = ""
    narrative: str = ""
    trigger: str = ""
    method: str = METHOD_CONDITIONAL
    horizon_days: int = 10
    driver_factors: list[str] = Field(default_factory=list)
    exposed_trades: list[str] = Field(default_factory=list)
    rationale: str = ""


class CalibratedScenario(BaseModel):
    """A proposal calibrated to concrete correlated driver shocks, ready to price."""

    name: str
    narrative: str = ""
    trigger: str = ""
    method: str = METHOD_CONDITIONAL
    horizon_days: int = 10
    legs: list[DriverLeg] = Field(default_factory=list)
    overlays: dict[str, float] = Field(default_factory=dict)
    world_view_id: str = ""
    # Faithfulness-verify outcome (P7): {n_dropped, verdicts:[{factor,verdict,reason}]} or None.
    verification: dict[str, Any] | None = None

    def to_definition_spec(self) -> dict[str, Any]:
        """A spec dict consumable by `ScenarioDefinition.from_config` (canonical-unit drivers)."""
        drivers = {leg.factor: float(leg.move) for leg in self.legs}
        return {
            "drivers": drivers,
            "overlays": dict(self.overlays),
            "method": self.method,
            "horizon_days": self.horizon_days,
            "narrative": self.narrative,
            "trigger": self.trigger,
        }


class CostSummary(BaseModel):
    """The LLM invocation cost rollup surfaced in the narrative + UI provenance."""

    total_cost_usd: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    n_llm_calls: int = 0
    n_tool_calls: int = 0
    per_stage: dict[str, dict[str, float]] = Field(default_factory=dict)


class ScenarioRanking(BaseModel):
    """One candidate's priced loss and whether it made the top-N cut (Mode 1)."""

    name: str
    total_pnl: float
    kept: bool = True


class ScenarioRejection(BaseModel):
    """A scenario the grounding gate refused: never priced, kept or published."""

    name: str
    reasons: list[str] = Field(default_factory=list)  # grounding-gate reason codes, or synthesis_failed
    detail: str = ""


class LLMScenarioArtifact(BaseModel):
    """The full record of one generation run (persisted under data/pricer/scenario_llm/)."""

    run_id: str
    mode: str  # comprehensive | hypothetical
    created_at: str = ""
    models: dict[str, str] = Field(default_factory=dict)
    # Provider provenance: profile, provider + model per stage, effort/thinking per model, SDK
    # versions (design §7). Empty in artifacts written before multi-provider support (= openai).
    llm: dict[str, Any] = Field(default_factory=dict)
    world_view_id: str = ""
    config_sig: str = ""
    prompt_sig: str = ""
    discovery: str = ""  # Mode 1: the ranked factor-universe summary from the discover phase
    proposals: list[ScenarioProposal] = Field(default_factory=list)
    calibrated: list[CalibratedScenario] = Field(default_factory=list)
    ranking: list[ScenarioRanking] = Field(default_factory=list)  # Mode 1: candidates by max loss
    rejected: list[ScenarioRejection] = Field(default_factory=list)  # refused by the grounding gate
    company_risk_factors: list[CompanyRiskFactor] = Field(default_factory=list)
    macro_risk_factors: list[MacroRiskFactor] = Field(default_factory=list)
    cost: CostSummary = Field(default_factory=CostSummary)
