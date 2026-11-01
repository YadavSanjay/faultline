"""Dual-RAG value-add attribution (P2).

Turns a scenario's dual-RAG provenance (``DualResolution.to_dict()``) into a governed,
UI-ready record of **what each service contributed vs the authored baseline** — the concrete
"extra information" MacroRAG and ARag added to the final shock, with the Δ each made, citations,
world-view snapshot, and ARag's fact-scaling. Fully deterministic (no LLM); an LLM narrative is
layered on separately (`narrative.py`) and never sets numbers.

Values are compared at the **unscaled driver level** (before the severity-tier multiplier), so
the attribution is the same across tiers — the service contribution is what it is; severity just
scales it. MacroRAG contributes *drivers* (macro legs); ARag contributes per-name *company legs*
(idiosyncratic, whether applied as an overlay or, for idiosyncratic-only scenarios, as the driver).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .scenario_gen import to_canonical


def _fmt(move: float, factor: str) -> str:
    """Human-readable move: rates/credit in bp, everything else in %."""
    if factor.startswith(("ir:", "credit", "fxrate:")):
        return f"{move * 1e4:+.0f}bp"
    return f"{move * 100:+.1f}%"


@dataclass
class LegContribution:
    factor: str
    role: str                          # "driver" | "overlay"
    final_move: float                  # unscaled move the service set
    baseline_move: float | None        # authored baseline for this factor (None = service-added)
    delta: float | None                # final - baseline (None when service-added)
    citations: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)   # service-specific evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor, "role": self.role, "final_move": self.final_move,
            "baseline_move": self.baseline_move, "delta": self.delta,
            "n_citations": len(self.citations), "citations": self.citations,
            "detail": self.detail,
        }


@dataclass
class ServiceContribution:
    service: str                       # "macrorag" | "arag"
    applied: bool
    status: str                        # applied | gate_failed | unavailable | not_configured
    legs: list[LegContribution] = field(default_factory=list)
    world_view_id: str = ""
    gate_passed: bool | None = None
    gate_findings: list[str] = field(default_factory=list)
    summary: str = ""

    @property
    def n_citations(self) -> int:
        return sum(len(leg.citations) for leg in self.legs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service, "applied": self.applied, "status": self.status,
            "n_legs": len(self.legs), "n_citations": self.n_citations,
            "world_view_id": self.world_view_id, "gate_passed": self.gate_passed,
            "gate_findings": self.gate_findings, "summary": self.summary,
            "legs": [leg.to_dict() for leg in self.legs],
        }


@dataclass
class ScenarioAttribution:
    scenario: str
    authored_baseline: dict[str, Any]
    macrorag: ServiceContribution
    arag: ServiceContribution
    headline_lines: list[str] = field(default_factory=list)
    narrative: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "authored_baseline": self.authored_baseline,
            "macrorag": self.macrorag.to_dict(),
            "arag": self.arag.to_dict(),
            "headline_lines": self.headline_lines,
            "narrative": self.narrative,
        }


def _authored_canonical(spec: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    drivers = {k: to_canonical(k, v) for k, v in spec.get("drivers", {}).items()}
    overlays = {k: to_canonical(k, v) for k, v in spec.get("overlays", {}).items()}
    return drivers, overlays


def _macro_contribution(dualrag: dict[str, Any],
                        authored_drivers: dict[str, float]) -> ServiceContribution:
    macro = dualrag.get("macro")
    if not macro:
        return ServiceContribution("macrorag", applied=False, status="not_configured")

    gate = macro.get("gate") or {}
    gate_passed = gate.get("passed")
    applied = bool(macro.get("used_macro"))
    if applied:
        status = "applied"
    elif gate_passed is False:
        status = "gate_failed"
    else:
        status = "unavailable"

    legs: list[LegContribution] = []
    drivers = macro.get("drivers", {})
    for factor, src in macro.get("sources", {}).items():
        if src.get("origin") != "macrorag":
            continue
        final = float(drivers.get(factor, 0.0))
        base = authored_drivers.get(factor)
        legs.append(LegContribution(
            factor=factor, role="driver", final_move=final,
            baseline_move=base, delta=(final - base) if base is not None else None,
            citations=list(src.get("chunk_ids", [])),
            detail={"macro_field": src.get("macro_field", ""),
                    "rationale": src.get("rationale", "")}))
    return ServiceContribution(
        "macrorag", applied=applied, status=status, legs=legs,
        world_view_id=str(macro.get("world_view_id", "")), gate_passed=gate_passed,
        gate_findings=list(gate.get("findings", [])), summary=str(macro.get("macro_summary", "")))


def _arag_contribution(dualrag: dict[str, Any], authored_drivers: dict[str, float],
                       authored_overlays: dict[str, float]) -> ServiceContribution:
    arag = dualrag.get("arag")
    if not arag:
        return ServiceContribution("arag", applied=False, status="not_configured")

    gate = arag.get("gate") or {}
    gate_passed = gate.get("passed")
    applied = bool(arag.get("used_arag"))
    driver_sources = dualrag.get("driver_sources", {})

    legs: list[LegContribution] = []
    for factor, move in arag.get("company_drivers", {}).items():
        src = arag.get("sources", {}).get(factor, {})
        # role: ARag legs become overlays when a systematic anchor remains, else drivers.
        role = "driver" if driver_sources.get(factor, {}).get("origin") == "arag" else "overlay"
        base = authored_drivers.get(factor)
        if base is None:
            base = authored_overlays.get(factor)
        cal = src.get("calibration") or {}
        legs.append(LegContribution(
            factor=factor, role=role, final_move=float(move),
            baseline_move=base, delta=(float(move) - base) if base is not None else None,
            citations=list(src.get("chunk_ids", [])),
            detail={"ticker": src.get("ticker", ""), "risk_factor": src.get("risk_factor", ""),
                    "seed_move": src.get("seed_move"), "fiscal_years": src.get("fiscal_years", []),
                    "calibration": cal}))
    status = "applied" if applied else ("gate_failed" if gate_passed is False else "not_configured")
    return ServiceContribution("arag", applied=applied, status=status, legs=legs,
                               gate_passed=gate_passed, gate_findings=list(gate.get("findings", [])))


def _headlines(macro: ServiceContribution, arag: ServiceContribution) -> list[str]:
    lines: list[str] = []
    if macro.applied and macro.legs:
        for leg in macro.legs:
            if leg.delta is not None and leg.baseline_move is not None:
                base = (f" (authored {_fmt(leg.baseline_move, leg.factor)}; "
                        f"delta {_fmt(leg.delta, leg.factor)})")
            else:
                base = " (new leg)"
            wv = f" · {macro.world_view_id}" if macro.world_view_id else ""
            lines.append(f"MacroRAG set {leg.factor} = {_fmt(leg.final_move, leg.factor)}{base} "
                         f"[{len(leg.citations)} cite(s)]{wv}")
    elif macro.status == "gate_failed":
        lines.append("MacroRAG: synthesis returned but failed the freshness/citation GATE "
                     "→ authored macro legs used")
    elif macro.status == "unavailable":
        lines.append("MacroRAG: no shock vector returned → authored macro legs used")

    for leg in arag.legs:
        cal = leg.detail.get("calibration") or {}
        if cal.get("applied"):
            exp = cal.get("exposure")
            exp_txt = f"{exp * 100:.0f}%" if isinstance(exp, (int, float)) else "?"
            sizing = (f"sized from a {_fmt(leg.detail.get('seed_move', 0.0), leg.factor)} seed "
                      f"x {cal.get('multiplier')} on {exp_txt} {cal.get('concept', 'exposure')} share")
        else:
            sizing = f"grounded at its {_fmt(leg.detail.get('seed_move', leg.final_move), leg.factor)} seed"
        lines.append(f"ARag {leg.role} {leg.factor} = {_fmt(leg.final_move, leg.factor)}: {sizing} "
                     f"[{len(leg.citations)} filing cite(s)]")
    return lines


def build_attribution(dualrag: dict[str, Any], authored_spec: dict[str, Any]) -> ScenarioAttribution:
    """Build the value-add attribution from a scenario's dual-RAG provenance + authored spec.

    ``dualrag`` is ``DualResolution.to_dict()`` (as pinned into the materialized provenance under
    ``"dualrag"``); ``authored_spec`` is the scenarios.json entry (the no-service baseline).
    """
    authored_drivers, authored_overlays = _authored_canonical(authored_spec)
    macro = _macro_contribution(dualrag, authored_drivers)
    arag = _arag_contribution(dualrag, authored_drivers, authored_overlays)
    return ScenarioAttribution(
        scenario=str(dualrag.get("scenario", "")),
        authored_baseline={"drivers": authored_drivers, "overlays": authored_overlays},
        macrorag=macro, arag=arag, headline_lines=_headlines(macro, arag))
