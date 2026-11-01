"""Deterministic fact-check on calibrated equity legs (harness-audit F4).

The P7 ``verify`` phase is an LLM judging an LLM. This pass is the *deterministic* half of
"doer != judge": for every company (equity ``eq:<TICKER>``) leg it checks, with **no model
call**, that

  * the leg is **grounded** — carries at least one citation;
  * its magnitude is inside the plausibility band ``[RETURN_FLOOR, max_up_move]`` — an
    implausible move (e.g. an LLM emitting ``eq:NVDA = -5.0``) is **clamped** to the bound
    rather than priced as-is, mirroring the materializer's own limited-liability floor;
  * when an ARag ``CoverageView`` is supplied, the ticker is actually **covered** and the
    covered fiscal years are within a freshness window (else ``UNCOVERED`` / ``STALE``).

Findings are attached to ``calibrated.verification["fact_check"]`` (merged with the LLM
verify block) so the Trace Explorer / UI can show exactly what ground truth said. Non-equity
legs (macro / ir / fx / credit) pass through untouched — ARag only grounds company legs.

Actions are conservative: an out-of-band magnitude is clamped (the scenario keeps its shape,
bounded); grounding / coverage / staleness issues are *flagged*, not dropped — surfacing them
to the analyst without silently rewriting the scenario. This never removes a leg, so it can
never empty a scenario the way an over-eager drop could.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ... import factor_keys as fk
from ..schema import CalibratedScenario

logger = logging.getLogger("scenariogen.llm.phases.fact_check")


@dataclass(frozen=True)
class CoverageView:
    """The ARag-covered tickers and their fiscal years — the ground truth for company legs."""

    fiscal_years_by_ticker: dict[str, frozenset[int]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> CoverageView:
        return cls({})

    @property
    def is_empty(self) -> bool:
        return not self.fiscal_years_by_ticker

    def covers(self, ticker: str) -> bool:
        return ticker.upper() in self.fiscal_years_by_ticker

    def fiscal_years(self, ticker: str) -> frozenset[int]:
        return self.fiscal_years_by_ticker.get(ticker.upper(), frozenset())

    @classmethod
    def from_items(cls, items: Any) -> CoverageView:
        out: dict[str, frozenset[int]] = {}
        for it in items or []:
            if not isinstance(it, dict):
                continue
            tkr = str(it.get("ticker") or "").upper()
            if not tkr:
                continue
            out[tkr] = frozenset(int(y) for y in it.get("fiscal_years", []) if y is not None)
        return cls(out)

    @classmethod
    def from_tool_json(cls, raw: str) -> CoverageView:
        """Parse an ARag ``/v1/coverage``-shaped ``{"items": [...]}`` payload; empty on any error."""
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return cls.empty()
        if isinstance(obj, dict):
            return cls.from_items(obj.get("items", []))
        return cls.empty()


@dataclass(frozen=True)
class FactCheckConfig:
    max_up_move: float = 0.95          # upper plausibility bound; RETURN_FLOOR (-0.95) is the lower
    freshness_years: int = 3
    require_coverage: bool = False     # when True, an equity leg with no coverage data is UNCOVERED


@dataclass
class LegVerdict:
    factor: str
    verdict: str                       # "OK" or comma-joined issue codes
    reason: str = ""
    original_move: float | None = None
    clamped_move: float | None = None

    @property
    def ok(self) -> bool:
        return self.verdict == "OK"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"factor": self.factor, "verdict": self.verdict, "reason": self.reason}
        if self.clamped_move is not None:
            d["original_move"] = self.original_move
            d["clamped_move"] = self.clamped_move
        return d


@dataclass
class FactCheckResult:
    calibrated: CalibratedScenario     # possibly with out-of-band equity moves clamped
    verdicts: list[LegVerdict] = field(default_factory=list)
    n_clamped: int = 0
    n_flagged: int = 0                 # ungrounded / uncovered / stale legs (flagged, not dropped)


_FLAG_CODES = ("UNGROUNDED", "UNCOVERED", "STALE")


def fact_check(
    calibrated: CalibratedScenario,
    *,
    coverage: CoverageView | None = None,
    as_of_year: int | None = None,
    config: FactCheckConfig | None = None,
) -> FactCheckResult:
    """Deterministically check each equity leg; clamp out-of-band moves; flag grounding/coverage.

    Pure and offline: no LLM, no network. ``coverage`` (if non-empty) enables the covered/stale
    checks; ``as_of_year`` enables staleness. Non-equity legs are returned unchanged.
    """
    config = config or FactCheckConfig()
    coverage = coverage or CoverageView.empty()
    lo, hi = fk.RETURN_FLOOR, config.max_up_move

    verdicts: list[LegVerdict] = []
    new_legs = list(calibrated.legs)
    n_clamped = n_flagged = 0

    for i, leg in enumerate(calibrated.legs):
        if not fk.is_equity(leg.factor):
            continue
        ticker = fk.equity_name(leg.factor)
        issues: list[str] = []
        reasons: list[str] = []

        if not leg.citations:
            issues.append("UNGROUNDED")
            reasons.append("no citation behind the company leg")

        if not coverage.is_empty:
            if not coverage.covers(ticker):
                issues.append("UNCOVERED")
                reasons.append(f"{ticker} not in ARag coverage")
            elif as_of_year is not None:
                yrs = coverage.fiscal_years(ticker)
                if yrs and max(yrs) < as_of_year - config.freshness_years:
                    issues.append("STALE")
                    reasons.append(f"newest covered filing FY{max(yrs)} older than "
                                   f"FY{as_of_year - config.freshness_years}")
        elif config.require_coverage:
            issues.append("UNCOVERED")
            reasons.append("no ARag coverage data available")

        clamped_move: float | None = None
        if leg.move < lo or leg.move > hi:
            clamped_move = max(lo, min(hi, leg.move))
            issues.append("CLAMPED")
            reasons.append(f"move {leg.move:+.3f} outside band [{lo:+.2f}, {hi:+.2f}] "
                           f"-> {clamped_move:+.3f}")
            new_legs[i] = leg.model_copy(update={"move": clamped_move})
            n_clamped += 1

        if not issues:
            verdicts.append(LegVerdict(factor=leg.factor, verdict="OK"))
            continue

        verdicts.append(LegVerdict(
            factor=leg.factor, verdict=",".join(issues), reason="; ".join(reasons),
            original_move=leg.move if clamped_move is not None else None,
            clamped_move=clamped_move))
        if any(c in issues for c in _FLAG_CODES):
            n_flagged += 1

    if n_clamped or n_flagged:
        logger.info("fact_check %r: %d clamped, %d flagged (equity legs)",
                    calibrated.name, n_clamped, n_flagged)

    merged = dict(calibrated.verification or {})
    merged["fact_check"] = {
        "n_clamped": n_clamped, "n_flagged": n_flagged,
        "verdicts": [v.to_dict() for v in verdicts],
    }
    updated = calibrated.model_copy(update={"legs": new_legs, "verification": merged})
    return FactCheckResult(updated, verdicts, n_clamped, n_flagged)
