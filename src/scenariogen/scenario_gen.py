"""Scenario generator: definition -> materialized correlated vector (S1).

A `ScenarioDefinition` is the small governed object of design §3: a narrative, a few
**drivers** (the factor moves the narrative asserts), a **propagation method**, a
**severity** scaling, and idiosyncratic **overlays** (key-man add-ons covariance can't
see). The `ScenarioGenerator` turns it into a complete, internally-consistent
`MaterializedScenario` (design §5E expert-anchored hybrid):

  1. scale the drivers by the severity multiplier,
  2. propagate to all other factors via the conditional-covariance engine (§5B),
  3. add idiosyncratic overlays on top,
  4. stamp provenance (method + covariance id) for reproducibility.

Driver/overlay magnitudes are authored in human units in config and parsed to the
canonical native units of `factor_keys` here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from . import factor_keys as fk
from .covariance import FactorCovariance
from .factor_vector import MaterializedScenario
from .versioning import compute_scenario_version

METHOD_CONDITIONAL = "conditional_covariance"
METHOD_DRIVERS_ONLY = "drivers_only"


def to_canonical(key: str, spec: Mapping[str, float] | float) -> float:
    """Parse a human-unit driver/overlay spec into a canonical native-unit move.

    Accepts a bare number (already canonical) or a single-key dict choosing the unit:
    ``move_bp`` (IR/credit, bp->decimal), ``move_pct`` (equity/FX, %->return),
    ``move_pts`` (vol points->decimal IV), ``move_rel`` (relative, as-is), or ``move``
    (raw canonical).
    """
    if isinstance(spec, (int, float)):
        return float(spec)
    if "move_bp" in spec:
        return float(spec["move_bp"]) * 1e-4
    if "move_pct" in spec:
        return float(spec["move_pct"]) / 100.0
    if "move_pts" in spec:
        return float(spec["move_pts"]) / 100.0
    if "move_rel" in spec:
        return float(spec["move_rel"])
    if "move" in spec:
        return float(spec["move"])
    raise ValueError(f"driver/overlay for {key!r} has no recognized unit key: {dict(spec)}")


@dataclass(frozen=True)
class ScenarioDefinition:
    """The authored, pre-propagation scenario (drivers + method + overlays)."""

    name: str
    drivers: dict[str, float]                 # canonical key -> native-unit move
    method: str = METHOD_CONDITIONAL
    severity: str = "adverse"
    severity_scale: float = 1.0               # multiplies driver magnitudes pre-propagation
    overlays: dict[str, float] = field(default_factory=dict)   # idiosyncratic, additive, post
    narrative: str = ""
    trigger: str = ""
    horizon_days: int = 10
    version: str = ""                          # content-hash of the economic definition

    @classmethod
    def from_config(cls, name: str, spec: Mapping[str, Any], *,
                    severity: str = "adverse", severity_scale: float = 1.0) -> ScenarioDefinition:
        drivers = {k: to_canonical(k, v) for k, v in spec.get("drivers", {}).items()}
        overlays = {k: to_canonical(k, v) for k, v in spec.get("overlays", {}).items()}
        return cls(
            name=name,
            drivers=drivers,
            method=str(spec.get("method", METHOD_CONDITIONAL)),
            severity=severity,
            severity_scale=float(severity_scale),
            overlays=overlays,
            narrative=str(spec.get("narrative", "")),
            trigger=str(spec.get("trigger", "")),
            horizon_days=int(spec.get("horizon_days", 10)),
            version=compute_scenario_version(dict(spec)),
        )


class ScenarioGenerator:
    """Materializes scenario definitions against an estimated covariance."""

    def __init__(self, covariance: FactorCovariance):
        self.cov = covariance

    def materialize(self, definition: ScenarioDefinition) -> MaterializedScenario:
        if not definition.drivers:
            raise ValueError(f"scenario {definition.name!r} has no drivers")

        scaled_drivers = {k: v * definition.severity_scale for k, v in definition.drivers.items()}

        if definition.method == METHOD_CONDITIONAL:
            known = {k: v for k, v in scaled_drivers.items() if k in self.cov.factors}
            unknown = {k: v for k, v in scaled_drivers.items() if k not in self.cov.factors}
            if not known:
                raise ValueError(
                    f"scenario {definition.name!r}: none of its drivers "
                    f"{list(scaled_drivers)} are covariance factors {self.cov.factors}")
            vector = self.cov.conditional_mean(known)
            vector.update(unknown)        # drivers outside Σ are applied directly
        elif definition.method == METHOD_DRIVERS_ONLY:
            vector = dict(scaled_drivers)
        else:
            raise ValueError(f"unknown method {definition.method!r}")

        # Idiosyncratic overlays add on top of the systematic (covariance-derived) move.
        for key, extra in definition.overlays.items():
            vector[key] = vector.get(key, 0.0) + extra

        # Limited-liability floor: a long equity/FX spot can't lose >100%, and the linear
        # propagation can imply sub--100% moves for high-beta names. Clamp so the applied
        # spot stays positive and the scenario is economically valid (plausibility then
        # flags the clamped move as beyond-historical).
        for key, move in vector.items():
            if fk.is_equity(key) or fk.is_fx(key):
                vector[key] = max(move, fk.RETURN_FLOOR)

        provenance: dict[str, Any] = {
            "scenario_version": definition.version,
            "method": definition.method,
            "severity_scale": definition.severity_scale,
            "horizon_days": definition.horizon_days,
            "drivers": dict(scaled_drivers),
            "overlays": dict(definition.overlays),
            "covariance": self.cov.as_dict(),
        }
        return MaterializedScenario(
            name=definition.name,
            vector=vector,
            drivers=tuple(definition.drivers),
            severity=definition.severity,
            provenance=provenance,
        )
