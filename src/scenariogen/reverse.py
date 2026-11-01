"""Reverse stress testing (S5, design §5D) — "what would break us?".

Instead of asking "what is the P&L of this scenario?", reverse stress asks "how severe a
move of *this shape* drives our loss past a limit?". We scale a scenario's narrative driver
vector by a severity multiplier λ (the same lever the tiers use) and **solve for the λ that
makes the full-revaluation loss hit a target** (e.g. an economic-loss limit), by bisection
over the monotone loss(lambda) curve. The answer is a limit-setting statement: "a Taiwan shock
at lambda~1.7x (between adverse 1.5 and severe 2.0) breaches the $50mm loss limit", or "even at
the max scale the book never loses $X — that limit is not reachable on this axis".

Built on the S1 generator (scale → conditional-covariance propagation → clamp) + the P2
full-revaluation `ScenarioEngine`, so the loss is exact (no Greek approximation).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from src.pricer.engine import PricingEngine
from src.pricer.input.common.trade import Trade
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.risk.scenarios import ScenarioEngine

from .scenario_gen import ScenarioDefinition, ScenarioGenerator


@dataclass
class ReverseStressResult:
    scenario: str
    target_loss: float                 # the loss limit we solved against (positive $)
    reachable: bool                    # can the loss be reached within max_scale?
    severity_scale: float              # λ* hitting the target (or max_scale if unreachable)
    achieved_loss: float               # full-reval loss at severity_scale (positive $)
    iterations: int = 0
    materialized: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario, "target_loss": self.target_loss,
            "reachable": self.reachable, "severity_scale": self.severity_scale,
            "achieved_loss": self.achieved_loss, "iterations": self.iterations,
            "materialized": self.materialized,
        }


class ReverseStressSolver:
    def __init__(self, engine: PricingEngine, generator: ScenarioGenerator, *,
                 max_scale: float = 10.0, tol_rel: float = 0.01, max_iter: int = 40):
        self.scen_engine = ScenarioEngine(engine)
        self.generator = generator
        self.max_scale = max_scale
        self.tol_rel = tol_rel
        self.max_iter = max_iter

    def loss_at(self, definition: ScenarioDefinition, scale: float,
                trades: list[Trade], ctx: ValuationContext) -> float:
        """Full-revaluation loss (positive $) of the scenario direction at severity λ=scale."""
        ms = self.generator.materialize(replace(definition, severity_scale=scale))
        pnls = self.scen_engine.run(ms, trades, ctx)
        return -sum(p.pnl for p in pnls)

    def solve(self, definition: ScenarioDefinition, trades: list[Trade],
              ctx: ValuationContext, target_loss: float) -> ReverseStressResult:
        """Find the smallest severity λ on this scenario's axis whose loss ≥ target_loss."""
        loss_max = self.loss_at(definition, self.max_scale, trades, ctx)
        if loss_max < target_loss:                         # limit not reachable on this axis
            return self._result(definition, target_loss, False, self.max_scale, loss_max, 0)

        lo, hi = 0.0, self.max_scale
        mid, f_mid, it = hi, loss_max, 0
        while it < self.max_iter:
            it += 1
            mid = 0.5 * (lo + hi)
            f_mid = self.loss_at(definition, mid, trades, ctx)
            if abs(f_mid - target_loss) <= self.tol_rel * target_loss:
                break
            if f_mid < target_loss:                        # loss increases with lambda
                lo = mid
            else:
                hi = mid
        return self._result(definition, target_loss, True, mid, f_mid, it)

    def _result(self, definition: ScenarioDefinition, target: float, reachable: bool,
                scale: float, loss: float, iterations: int) -> ReverseStressResult:
        ms = self.generator.materialize(replace(definition, severity_scale=scale))
        return ReverseStressResult(
            scenario=definition.name, target_loss=target, reachable=reachable,
            severity_scale=scale, achieved_loss=loss, iterations=iterations,
            materialized=ms.to_dict())
