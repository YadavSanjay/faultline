"""Governance reads for the UI (Phase 6) — from the stored governance report (V1 stored-only).

The governance runner writes a provenance-stamped report per run
(``data/pricer/governance/<date>/governance_<run_id>.json``) with the three gates: golden
regression, IPV (independent price verification), and model-inventory coverage. The control
room (``/governance/posture``) already rolls these up; these endpoints serve the detail.
Drift is the run-over-run PV-drift count from the pricer manifests.
"""

from __future__ import annotations

from typing import Any

from .stores import latest_governance_report, recent_runs


def report() -> dict[str, Any]:
    """Full governance report (G3 IPV + G4 golden + inventory coverage)."""
    gov = latest_governance_report()
    if gov is None:
        return {"available": False, "reason": "no governance run found — run `python -m src.pricer.governance_runner`"}
    return {"available": True, **gov}


def drift() -> dict[str, Any]:
    """PV-drift posture (G6): run-over-run drift alert counts from the pricer manifests."""
    pricer_runs = recent_runs(limit=20, module="pricer")
    timeline = [
        {"run_id": r["run_id"], "date": r["date"], "finished_at": r["finished_at"],
         "n_drift_alerts": int(r["counts"].get("n_drift_alerts", 0) or 0)}
        for r in pricer_runs
    ]
    latest = timeline[0] if timeline else None
    return {
        "available": bool(timeline),
        "latest": latest,
        "timeline": timeline,
        "note": ("Drift fires when a trade's PV moves beyond tolerance vs the prior run. "
                 "Per-alert detail needs the drift store (not persisted in V1)."),
    }
