"""Book risk-limit monitoring (P2 / item 7).

Compares the run's VaR and aggregate book sensitivities against configured limits and emits
a breach per violation. This turns the risk report from a standalone artifact into a control:
a breach increments `risk_limit_breach_total{limit}` (→ Alertmanager) and is logged, so an
operator is paged when the book exceeds its mandate.

Config (`pricer_config.json` → `risk.limits`):
  var:          {"0.99": <max loss>}            — VaR limit per confidence level
  sensitivities {"CS01": <max>, "IR_PARALLEL": <max>, ...}  — abs-value limit per factor
Limits are desk policy; the shipped values are placeholders to be set at go-live.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.common.obs import metrics as obs_metrics

if TYPE_CHECKING:
    from .var import VarResult

RISK_LIMIT_BREACH = obs_metrics.counter(
    "risk", "limit_breach_total", "Book risk-limit breaches", ("limit",))


@dataclass
class LimitBreach:
    name: str          # e.g. "VaR[0.99]" or "CS01"
    kind: str          # "var" | "sensitivity"
    value: float       # observed (VaR loss, or abs sensitivity)
    limit: float       # configured ceiling

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "value": self.value, "limit": self.limit}


class LimitMonitor:
    def __init__(self, config: dict[str, Any]):
        self.cfg = config.get("risk", {}).get("limits", {}) or {}

    def check(self, var_result: VarResult | None,
              book_sensitivities: dict[str, float]) -> list[LimitBreach]:
        breaches: list[LimitBreach] = []

        if var_result is not None:
            for lvl, limit in (self.cfg.get("var", {}) or {}).items():
                observed = var_result.var.get(str(lvl))
                if observed is not None and observed > float(limit):
                    breaches.append(LimitBreach(f"VaR[{lvl}]", "var", float(observed), float(limit)))

        for factor, limit in (self.cfg.get("sensitivities", {}) or {}).items():
            # A limit on an aggregate factor (e.g. "CS01") also covers its per-bucket keys
            # ("CS01:IG", "CS01:HY") once the book keys credit by rating (Tier 3.9).
            if factor in book_sensitivities:
                observed = abs(book_sensitivities[factor])
            else:
                observed = abs(sum(v for k, v in book_sensitivities.items()
                                   if k.startswith(f"{factor}:")))
            if observed > float(limit):
                breaches.append(LimitBreach(factor, "sensitivity", observed, float(limit)))

        for b in breaches:
            RISK_LIMIT_BREACH.labels(limit=b.name).inc()
        return breaches
