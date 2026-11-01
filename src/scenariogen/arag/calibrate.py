"""Deterministic company-leg fact-scaling (dual-RAG P1).

The user-selected policy: an authored/seed company move is **scaled** by a cited ARag fact so
the shock size reflects the company's real, disclosed exposure — fully deterministic, no LLM.

    scaled_move = seed_move * multiplier
    multiplier  = clamp(1 + elasticity * (exposure - baseline), min_scale, max_scale)

``exposure`` is a normalized quantity in the spirit of [0, 1]:
  * ``mode="share"`` — a revenue/segment share: ``numerator / denominator`` from ARag
    ``facts_lookup`` at one fiscal year (e.g. Data-Center Revenue / total Revenue = 0.88).
  * ``mode="raw"``   — a single fact used directly against ``baseline`` (for facts already a
    ratio, or a level the caller pre-normalizes via ``baseline``/scales).

Calibration is **best-effort per leg**: if the fact is unavailable (NOT_FOUND / zero
denominator / ARag returns nothing), the multiplier is 1.0 and ``applied=False`` with a note —
the leg keeps its seed magnitude and its Risk-Factor grounding. (Service-level failures — a
timeout past the 10-min ceiling — are raised by the client and handled fail-loud upstream; they
never reach here as a silent 1.0.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client import AragClient
from .models import AragFactPoint


@dataclass
class CompanyCalibration:
    """Per-company fact-scaling spec (from ``arag.json`` -> companies[].calibration)."""

    concept: str
    fiscal_year: int
    mode: str = "share"                         # "share" | "raw"
    dimension: dict[str, str] | None = None     # numerator slice, e.g. {"segment": "Data Center"}
    denominator_concept: str = ""               # defaults to ``concept``
    fiscal_period: str = "FY"
    elasticity: float = 0.5
    baseline: float = 0.5
    min_scale: float = 0.5
    max_scale: float = 2.0

    @classmethod
    def from_config(cls, spec: dict[str, Any]) -> CompanyCalibration:
        return cls(
            concept=str(spec["concept"]),
            fiscal_year=int(spec["fiscal_year"]),
            mode=str(spec.get("mode", "share")),
            dimension=dict(spec["dimension"]) if spec.get("dimension") else None,
            denominator_concept=str(spec.get("denominator_concept", "")),
            fiscal_period=str(spec.get("fiscal_period", "FY")),
            elasticity=float(spec.get("elasticity", 0.5)),
            baseline=float(spec.get("baseline", 0.5)),
            min_scale=float(spec.get("min_scale", 0.5)),
            max_scale=float(spec.get("max_scale", 2.0)),
        )


@dataclass
class CalibrationResult:
    """The outcome of scaling one leg — auditable: fact -> exposure -> multiplier."""

    applied: bool
    multiplier: float
    mode: str = ""
    concept: str = ""
    fiscal_year: int = 0
    exposure: float | None = None
    numerator: float | None = None
    denominator: float | None = None
    baseline: float = 0.0
    elasticity: float = 0.0
    citation_chunk_ids: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied, "multiplier": round(self.multiplier, 4),
            "mode": self.mode, "concept": self.concept, "fiscal_year": self.fiscal_year,
            "exposure": self.exposure, "numerator": self.numerator,
            "denominator": self.denominator, "baseline": self.baseline,
            "elasticity": self.elasticity, "citation_chunk_ids": list(self.citation_chunk_ids),
            "note": self.note,
        }


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _pick(points: list[AragFactPoint], fiscal_year: int) -> AragFactPoint | None:
    for p in points:
        if p.fiscal_year == fiscal_year and p.status == "OK" and p.value is not None:
            return p
    return None


def _multiplier(cal: CompanyCalibration, exposure: float) -> float:
    raw = 1.0 + cal.elasticity * (exposure - cal.baseline)
    return _clamp(raw, cal.min_scale, cal.max_scale)


def _cite(*points: AragFactPoint | None) -> list[str]:
    ids: list[str] = []
    for p in points:
        if p is not None and p.citation:
            cid = p.citation.get("chunk_id") or p.citation.get("fact_id")
            if cid:
                ids.append(str(cid))
    return ids


def _no_scale(cal: CompanyCalibration, note: str) -> CalibrationResult:
    return CalibrationResult(applied=False, multiplier=1.0, mode=cal.mode, concept=cal.concept,
                             fiscal_year=cal.fiscal_year, baseline=cal.baseline,
                             elasticity=cal.elasticity, note=note)


def resolve_multiplier(client: AragClient, cal: CompanyCalibration, *, ticker: str) -> CalibrationResult:
    """Compute the deterministic scaling multiplier for one company leg from ARag facts.

    Best-effort: any missing/zero fact yields ``applied=False, multiplier=1.0`` with a note,
    so the caller keeps the seed magnitude. Never raises on absent data (only the transport
    raises, on a timeout/HTTP error, which the caller handles fail-loud).
    """
    fy = cal.fiscal_year
    if cal.mode == "raw":
        series = client.facts_lookup(ticker, cal.concept, [fy], fiscal_period=cal.fiscal_period).series
        pt = _pick(series, fy)
        if pt is None or pt.value is None:
            return _no_scale(cal, f"{cal.concept} FY{fy} unavailable")
        exposure = float(pt.value)
        return CalibrationResult(
            applied=True, multiplier=_multiplier(cal, exposure), mode=cal.mode,
            concept=cal.concept, fiscal_year=fy, exposure=exposure, numerator=exposure,
            baseline=cal.baseline, elasticity=cal.elasticity, citation_chunk_ids=_cite(pt),
            note="raw fact",
        )

    # mode == "share"
    den_concept = cal.denominator_concept or cal.concept
    num_series = client.facts_lookup(ticker, cal.concept, [fy], dimension=cal.dimension,
                                     fiscal_period=cal.fiscal_period).series
    den_series = client.facts_lookup(ticker, den_concept, [fy],
                                     fiscal_period=cal.fiscal_period).series
    num, den = _pick(num_series, fy), _pick(den_series, fy)
    if num is None or num.value is None:
        return _no_scale(cal, f"{cal.concept}{cal.dimension or ''} FY{fy} unavailable")
    if den is None or den.value is None or den.value == 0:
        return _no_scale(cal, f"{den_concept} FY{fy} denominator unavailable/zero")
    exposure = float(num.value) / float(den.value)
    return CalibrationResult(
        applied=True, multiplier=_multiplier(cal, exposure), mode=cal.mode, concept=cal.concept,
        fiscal_year=fy, exposure=exposure, numerator=float(num.value),
        denominator=float(den.value), baseline=cal.baseline, elasticity=cal.elasticity,
        citation_chunk_ids=_cite(num, den), note="share = numerator/denominator",
    )
