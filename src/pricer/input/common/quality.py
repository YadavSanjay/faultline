"""Data-quality policy (P0c): governed staleness + schema gating.

Pure-domain definitions (no pandas/QuantLib) so the input layer can carry the policy
on the `ValuationContext`. The loader *enforces* it; this module only *describes* it.

A breach (stale snapshot, missing snapshot, broken schema) maps to one of:
  * WARN       — record a warning and continue (today's behaviour; the default).
  * BLOCK      — refuse to price; raise `DataQualityError` so the trade fails fast
                 with a clear message instead of erroring deep inside QuantLib.
  * SUBSTITUTE — do not block; let the adapter fall back to a configured proxy
                 (e.g. the issuer Z-spread / CDS fallbacks). For pure staleness this
                 behaves like WARN (there is no fresher value to substitute).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class QualityAction(str, Enum):
    WARN = "warn"
    BLOCK = "block"
    SUBSTITUTE = "substitute"


class DataQualityError(RuntimeError):
    """Raised when a BLOCK-policy data-quality breach prevents pricing."""


class DataContractError(DataQualityError):
    """A required column / schema expectation was violated on an input file."""


@dataclass(frozen=True)
class QualityPolicy:
    """Per-data-kind quality policy. `kind` keys match the adapter call sites
    (`ois_curve`, `treasury_curve`, `equity_spot`, `equity_chain`, `fx_spot`,
    `fx_rate`, ...). Tolerance precedence is: per-kind override > the run's
    `ValuationContext.max_data_staleness_days` baseline.
    """

    default_action: QualityAction = QualityAction.WARN
    actions: dict[str, QualityAction] = field(default_factory=dict)
    staleness_days: dict[str, int] = field(default_factory=dict)

    def action_for(self, kind: str | None) -> QualityAction:
        if kind is None:
            return self.default_action
        return self.actions.get(kind, self.default_action)

    def tolerance_for(self, kind: str | None, baseline: int) -> int:
        if kind is None:
            return baseline
        return self.staleness_days.get(kind, baseline)

    @classmethod
    def from_config(cls, config: dict) -> QualityPolicy:
        dq = config.get("data_quality", {}) or {}
        default_action = QualityAction(dq.get("default_action", "warn"))
        actions: dict[str, QualityAction] = {}
        staleness: dict[str, int] = {}
        for kind, spec in (dq.get("kinds", {}) or {}).items():
            if "action" in spec:
                actions[kind] = QualityAction(spec["action"])
            if "max_staleness_days" in spec:
                staleness[kind] = int(spec["max_staleness_days"])
        return cls(default_action=default_action, actions=actions, staleness_days=staleness)

    def describe(self) -> dict:
        return {
            "default_action": self.default_action.value,
            "actions": {k: v.value for k, v in self.actions.items()},
            "staleness_days": dict(self.staleness_days),
        }


def default_policy() -> QualityPolicy:
    """Permissive policy (WARN everything) — preserves pre-P0c behaviour."""
    return QualityPolicy()
