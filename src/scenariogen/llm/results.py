"""Pricing result types for the LLM flow (P2).

A light module (no QuantLib / pricer imports) so the flow and phases can depend on the priced
result shape without pulling the heavy pricing stack. `ScenarioPricingService` in `pricing.py`
produces these from the existing engine; tests inject a fake `PricingService`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..scenario_gen import ScenarioDefinition


@dataclass
class PricedScenario:
    """The priced outcome of one scenario, decoupled from the pricer's internal result type."""

    name: str
    total_pnl: float
    pnl_by_asset_class: dict[str, float]
    capital_delta: float | None = None
    es_base: float | None = None
    es_shocked: float | None = None
    plausibility_status: str = "n/a"
    vector: dict[str, float] = field(default_factory=dict)


class PricingService(Protocol):
    """Prices a calibrated scenario definition through the existing full-reval engine."""

    def price(self, definition: ScenarioDefinition) -> PricedScenario: ...
