"""Scenario stress-report aggregation + persistence (S2).

Bundles the per-scenario/per-severity P&L and ES-under-shock capital into one auditable
artifact under ``data/pricer/scenario/<valuation_date>/`` with run/snapshot/covariance
provenance, so a stress number is reproducible from its pinned inputs (design §9).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.common import storage

from .plausibility import PlausibilityResult
from .stress import ScenarioStressResult


@dataclass
class StressReport:
    valuation_date: str
    run_id: str
    snapshot_id: str
    covariance: dict[str, Any] = field(default_factory=dict)
    results: list[ScenarioStressResult] = field(default_factory=list)
    # Scenarios stopped fail-loud during dual-RAG generation (service timeout / GATE / no seed);
    # surfaced so the UI can show "stopped — ARag unavailable" rather than silently omitting them.
    stopped: list[dict[str, Any]] = field(default_factory=list)
    # Rollup of the run's captured call-trace (P3): n_calls, cost/latency by service. The full
    # per-call records live in the sidecar JSONL under scenario/<date>/trace/<run_id>.jsonl.
    trace: dict[str, Any] | None = None
    # Scenarios whose severe tier loses less than their adverse tier (tier_order_flags): the severe tier
    # propagates through crisis Σ, so a convex book or a different crisis structure can do this.
    tier_flags: list[dict[str, Any]] = field(default_factory=list)
    # Tiers the plausibility gate held out, with their findings (gate-findings-recorded).
    held_out: list[dict[str, Any]] = field(default_factory=list)
    # Per-source coverage, staleness and holds rolled up into an overall status (signal-health).
    signal_health: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valuation_date": self.valuation_date,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "covariance": self.covariance,
            "scenarios": [r.to_dict() for r in self.results],
            "stopped": self.stopped,
            "trace": self.trace,
            "tier_flags": self.tier_flags,
            "held_out": self.held_out,
            "signal_health": self.signal_health,
        }


def tier_order_flags(results: list[ScenarioStressResult]) -> list[dict[str, Any]]:
    """Scenarios whose severe tier loses less than their adverse tier.

    Args:
        results: The run's results (one per scenario x tier that ran).

    Returns:
        One ``{"scenario", "flag", "adverse_pnl", "severe_pnl"}`` per scenario with both tiers whose severe
        P&L is above (a smaller loss than) its adverse P&L, in result order.
    """
    by: dict[str, dict[str, float]] = {}
    for r in results:
        by.setdefault(r.scenario, {})[r.severity] = r.total_pnl
    return [{"scenario": name, "flag": "severe_below_adverse", "adverse_pnl": tiers["adverse"],
             "severe_pnl": tiers["severe"]}
            for name, tiers in by.items()
            if "adverse" in tiers and "severe" in tiers and tiers["severe"] > tiers["adverse"]]


def held_out_entry(scenario: str, severity: str, version: str, result: PlausibilityResult) -> dict[str, Any]:
    """The report record of a scenario tier the plausibility gate held out of the run.

    Args:
        scenario: The scenario name.
        severity: The tier (e.g. ``"adverse"`` or ``"severe"``).
        version: The scenario version the gate checked.
        result: The gate's result for that tier; not mutated.

    Returns:
        ``{"scenario", "severity", "scenario_version", "status", "findings"}``: ``status`` is
        ``result.status`` and ``findings`` is ``[f.as_dict() for f in result.findings]`` with the
        ``"fail"`` findings first, then ``"flag"``, then any other severity, each group in its
        original order.
    """
    order = {"fail": 0, "flag": 1}
    findings = [f.as_dict() for f in result.findings]
    findings.sort(key=lambda d: order.get(str(d.get("severity")), 2))      # stable within groups
    return {"scenario": scenario, "severity": severity, "scenario_version": version,
            "status": result.status, "findings": findings}


def held_out_summary(entry: Mapping[str, Any], limit: int = 5) -> str:
    """One log line saying why a held-out tier failed the gate.

    Args:
        entry: A record built by :func:`held_out_entry`.
        limit: The most findings to spell out; at least 1.

    Returns:
        ``"<n_fail> fail / <n> finding(s): "`` then up to ``limit`` findings in entry order, each as
        ``"<factor> <check> <observed> vs <limit>"`` (numbers formatted ``:.4g``) joined by ``"; "``,
        then ``" (+<k> more)"`` when ``k > 0`` findings are left out.

    Raises:
        ValueError: If ``limit`` is below 1.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    findings = list(entry.get("findings", []))
    n_fail = sum(1 for f in findings if f.get("severity") == "fail")
    parts = [f"{f.get('factor')} {f.get('check')} {float(f.get('observed')):.4g} vs {float(f.get('limit')):.4g}"
             for f in findings[:limit]]
    more = len(findings) - len(parts)
    return (f"{n_fail} fail / {len(findings)} finding(s): " + "; ".join(parts)
            + (f" (+{more} more)" if more > 0 else ""))


def write_stress_report(report: StressReport, project_root: Path) -> Path | str:
    return _atomic_write(report.to_dict(), project_root, report.valuation_date,
                         f"scenario_{report.run_id}.json")


@dataclass
class ReverseStressReport:
    """Reverse-stress run (S5, design §5D): per scenario, the severity λ that drives the
    full-revaluation loss to the loss limit — persisted so the "what would break us?" answer
    is auditable and surfaceable, not just logged. ``results`` are ``ReverseStressResult``
    dicts (each carrying the materialized breaking-shock vector)."""

    valuation_date: str
    run_id: str
    snapshot_id: str
    target_loss: float
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valuation_date": self.valuation_date,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "target_loss": self.target_loss,
            "results": self.results,
        }


def write_reverse_stress_report(report: ReverseStressReport, project_root: Path) -> Path | str:
    return _atomic_write(report.to_dict(), project_root, report.valuation_date,
                         f"reverse_{report.run_id}.json")


def _atomic_write(payload: dict[str, Any], project_root: Path, valuation_date: str,
                  filename: str) -> Path | str:
    out = storage.join(
        storage.data_base(project_root), "pricer", "scenario", valuation_date, filename)
    storage.write_text_atomic(out, json.dumps(payload, indent=2, default=str))
    return out if storage.is_remote_uri(out) else Path(out)
