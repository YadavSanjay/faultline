"""Scenario plausibility / GATE checks (S3, design §9).

Before a materialized scenario enters the production run it must be *plausible*:

  bound check     each per-factor move is within a hard sanity cap for its factor class
                  (catches authoring errors and runaway propagation) -> violation = FAIL.
                  This is the hard gate.
  envelope check  each move sits within an n-sigma historical envelope (daily std from Σ,
                  scaled to the scenario horizon). Stress moves are *designed* to exceed
                  ordinary history, so an envelope breach is by default a FLAG ("beyond-
                  historical", recorded for review) rather than a FAIL — the per-tier action
                  is config-driven (`severe_action` / `non_severe_action`: flag|fail|ignore),
                  so a stricter desk can make non-severe breaches gate.

A scenario is **promotable** unless a check FAILs. The GATE (in the runner) holds
non-promotable scenarios out of the production run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import factor_keys as fk
from .covariance import FactorCovariance
from .factor_vector import MaterializedScenario

# Bound config keys per factor class, and the natural unit each cap is expressed in.
_BOUND_UNIT = {"ir": "bp", "equity": "pct", "fx": "pct", "eqvol": "pts", "ratesvol": "rel",
               "credit": "bp", "style": "pct", "basis": "pct", "basis_bp": "bp",
               "rates_decomp": "bp"}


def factor_class(key: str) -> str:
    if fk.is_ir(key):
        return "ir"
    if fk.is_equity(key):
        return "equity"
    if fk.is_fx(key):
        return "fx"
    if fk.is_style(key):
        return "style"
    if fk.is_basis(key):
        # The two spread bases (swap-spread, CDS-bond) are quoted in bp; the ETF dispersion in pct.
        return "basis_bp" if key in fk.BP_BASIS_KEYS else "basis"
    if fk.is_rates_decomp(key):
        return "rates_decomp"    # real / breakeven / term-premium drivers (F6), bp-quoted
    if fk.is_issuer_credit(key):
        return "credit"          # credit:<ISSUER> shares the credit bound (issuer-credit-factors)
    return key  # "eqvol" | "ratesvol" | "credit"


def hard_bound(bounds: dict[str, Any], key: str) -> float | None:
    """The hard plausibility bound of a factor as a canonical decimal magnitude (None if unbounded)."""
    cls = factor_class(key)
    spec, unit = bounds.get(cls), _BOUND_UNIT.get(cls)
    if not spec or unit is None or f"max_abs_{unit}" not in spec:
        return None
    cap = float(spec[f"max_abs_{unit}"])
    if unit == "bp":
        return cap * 1e-4
    return cap / 100.0 if unit in ("pct", "pts") else cap


def _to_unit(move: float, unit: str) -> float:
    """Canonical decimal move -> the cap's natural unit (for comparison/reporting)."""
    if unit == "bp":
        return move / 1e-4
    if unit in ("pct", "pts"):
        return move * 100.0
    return move  # rel


@dataclass
class PlausibilityFinding:
    factor: str
    check: str            # "bound" | "envelope" | "market_implied" | "market_unpriced"
    observed: float       # the move, in the check's unit (market_implied: the probability)
    limit: float          # the cap, in the check's unit
    severity: str         # "fail" | "flag"

    def as_dict(self) -> dict[str, Any]:
        return {"factor": self.factor, "check": self.check, "observed": self.observed,
                "limit": self.limit, "severity": self.severity}


@dataclass
class PlausibilityResult:
    scenario: str
    status: str = "pass"                       # pass | flag | fail
    findings: list[PlausibilityFinding] = field(default_factory=list)

    @property
    def promotable(self) -> bool:
        return self.status != "fail"

    def _record(self, finding: PlausibilityFinding) -> None:
        self.findings.append(finding)
        if finding.severity == "fail":
            self.status = "fail"
        elif finding.severity == "flag" and self.status != "fail":
            self.status = "flag"

    def to_dict(self) -> dict[str, Any]:
        return {"scenario": self.scenario, "status": self.status, "promotable": self.promotable,
                "findings": [f.as_dict() for f in self.findings]}


class PlausibilityChecker:
    def __init__(self, covariance: FactorCovariance, config: dict[str, Any],
                 market: Any | None = None):
        self.cov = covariance
        self.bounds: dict[str, dict[str, float]] = config.get("bounds", {})
        env = config.get("envelope", {})
        self.n_sigma = float(env.get("n_sigma", 6.0))
        # per-tier action on an envelope breach: "flag" (record, still promotable) |
        # "fail" (gate out) | "ignore" (skip the check).
        self.severe_action = str(env.get("severe_action", "flag"))
        self.non_severe_action = str(env.get("non_severe_action", "flag"))
        # Forward-looking market-implied check (B4). `market` is an OptionSignalProvider or None.
        # It is strictly ADDITIVE and FLAG-ONLY: it records how extreme each equity move is versus
        # the option market's risk-neutral distribution but never FAILs, so gating is unchanged when
        # it is wired in (and it is a no-op entirely when `market is None`).
        self.market = market
        mi = config.get("market_implied", {})
        self.mi_enabled = bool(mi.get("enabled", True)) and market is not None
        self.mi_min_prob = float(mi.get("min_prob", 0.01))    # flag moves the market prices below this

    def check(self, scenario: MaterializedScenario) -> PlausibilityResult:
        horizon = int(scenario.provenance.get("horizon_days", 1)) or 1
        result = PlausibilityResult(scenario.name)
        for key, move in scenario.vector.items():
            self._bound_check(result, key, move)
            self._envelope_check(result, key, move, horizon, scenario.severity)
            if self.mi_enabled:
                self._market_implied_check(result, key, move)
        return result

    def _market_implied_check(self, result: PlausibilityResult, key: str, move: float) -> None:
        """Flag (never fail) an equity move the option market prices as very unlikely.

        For each ``eq:<TKR>`` move, the RND gives ``P`` = the market-implied probability of a move
        at least this extreme. A stress move is *meant* to be in the tail, so this is a FLAG that
        records the market-implied percentile alongside the scenario — it does not gate anything out.
        """
        if self.market is None or not fk.is_equity(key) or move == 0.0:
            return
        prob = self.market.implied_prob(fk.equity_name(key), move)
        if prob is None or prob >= self.mi_min_prob:
            return
        if prob <= 0.0:
            # No option-implied mass at or beyond the move: the chain does not price it, which is not
            # the same as a market-implied probability of zero. Record the move, not a probability.
            result._record(PlausibilityFinding(
                key, "market_unpriced", observed=move, limit=0.0, severity="flag"))
            return
        result._record(PlausibilityFinding(
            key, "market_implied", observed=prob, limit=self.mi_min_prob, severity="flag"))

    def _bound_check(self, result: PlausibilityResult, key: str, move: float) -> None:
        cls = factor_class(key)
        spec = self.bounds.get(cls)
        if not spec:
            return
        unit = _BOUND_UNIT[cls]
        cap = float(spec.get(f"max_abs_{unit}", math.inf))
        observed = _to_unit(move, unit)
        if abs(observed) > cap:
            result._record(PlausibilityFinding(key, "bound", observed, cap, "fail"))

    def _envelope_check(self, result: PlausibilityResult, key: str, move: float,
                        horizon: int, severity: str) -> None:
        action = self.severe_action if severity == "severe" else self.non_severe_action
        if action == "ignore" or key not in self.cov.factors:
            return                                    # no historical series -> bound check only
        idx = self.cov.index(key)
        daily_std = math.sqrt(max(float(self.cov.cov[idx, idx]), 0.0))
        if daily_std <= 0.0:
            return
        band = self.n_sigma * daily_std * math.sqrt(horizon)
        if abs(move) > band:
            result._record(PlausibilityFinding(
                key, "envelope", move, band, "fail" if action == "fail" else "flag"))
