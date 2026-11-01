"""Risk-appetite profile definitions.

A profile is a full sizing mandate: how much risk to run (VaR budget as a fraction of
the capital base), how much leverage is allowed, how concentrated the risk may get, how
the directional book is allocated (equal risk vs return-tilted), and how much of the
hedge book to carry. Presets live in ``configs/pricer/sizing.json`` and are loaded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RiskProfile:
    key: str
    label: str
    var_budget_pct: float        # 1-day 99% VaR target as a fraction of capital base
    gross_leverage_cap: float    # max gross notional / capital base (guardrail)
    concentration_cap: float     # max single directional trade's share of the risk BUDGET (intended)
    risk_share_cap: float        # max single directional trade's share of REALIZED net-book risk (1.0=off)
    notional_cap_pct: float      # max single trade's gross notional / capital base
    hedge_scale: float           # multiplier on each hedge's current size (1.0 = full)
    method: str                  # "erc" (equal risk) | "return_tilt"
    tilt_power: float            # Sharpe exponent for return-tilted budgets (0 => equal)
    sharpe_floor: float          # min budget share weight so nothing is fully excluded
    description: str = ""

    @classmethod
    def from_config(cls, key: str, d: dict[str, Any]) -> RiskProfile:
        return cls(
            key=key,
            label=str(d.get("label", key.title())),
            var_budget_pct=float(d["var_budget_pct"]),
            gross_leverage_cap=float(d["gross_leverage_cap"]),
            concentration_cap=float(d["concentration_cap"]),
            risk_share_cap=float(d.get("risk_share_cap", 1.0)),
            notional_cap_pct=float(d.get("notional_cap_pct", 0.20)),
            hedge_scale=float(d["hedge_scale"]),
            method=str(d.get("method", "erc")),
            tilt_power=float(d.get("tilt_power", 0.0)),
            sharpe_floor=float(d.get("sharpe_floor", 0.05)),
            description=str(d.get("description", "")),
        )


def load_profiles(cfg: dict[str, Any]) -> dict[str, RiskProfile]:
    profiles = cfg.get("profiles", {})
    return {k: RiskProfile.from_config(k, v) for k, v in profiles.items()}
