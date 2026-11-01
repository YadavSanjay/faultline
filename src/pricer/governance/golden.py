"""Golden / regression PV gate (P4).

Compares freshly computed PVs against frozen known-good values pinned to an immutable
market-data snapshot + valuation date (configs/pricer/golden_pvs.json). Any drift beyond
tolerance — from a refactor, a QuantLib upgrade, or changed market data — is flagged, so
prices can never move silently. A snapshot mismatch is itself a finding (the pinned inputs
changed → review and re-baseline).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_project_root


@dataclass(frozen=True)
class GoldenBaseline:
    valuation_date: str
    snapshot_id: str
    quantlib_version: str
    rel_tol: float
    abs_tol: float
    pvs: dict[str, float]

    @classmethod
    def load(cls, path: Path | None = None) -> GoldenBaseline:
        path = path or get_project_root() / "configs" / "pricer" / "golden_pvs.json"
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return cls(
            valuation_date=d["valuation_date"], snapshot_id=d["snapshot_id"],
            quantlib_version=d.get("quantlib_version", ""),
            rel_tol=float(d.get("rel_tol", 1e-6)), abs_tol=float(d.get("abs_tol", 0.01)),
            pvs={k: float(v) for k, v in d["pvs"].items()},
        )


@dataclass
class GoldenBreak:
    trade_id: str
    expected: float
    actual: float

    @property
    def abs_diff(self) -> float:
        return self.actual - self.expected

    @property
    def rel_diff(self) -> float:
        return self.abs_diff / self.expected if self.expected else float("inf")

    def as_dict(self) -> dict[str, Any]:
        return {"trade_id": self.trade_id, "expected": self.expected,
                "actual": self.actual, "abs_diff": self.abs_diff, "rel_diff": self.rel_diff}


@dataclass
class GoldenResult:
    snapshot_id: str
    snapshot_matches: bool
    n_checked: int
    breaks: list[GoldenBreak] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # golden ids not priced this run

    @property
    def passed(self) -> bool:
        return not self.breaks and not self.missing and self.snapshot_matches

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "snapshot_id": self.snapshot_id,
            "snapshot_matches": self.snapshot_matches,
            "n_checked": self.n_checked,
            "n_breaks": len(self.breaks),
            "breaks": [b.as_dict() for b in self.breaks],
            "missing": self.missing,
        }


def check_golden(pvs: dict[str, float], snapshot_id: str,
                 baseline: GoldenBaseline | None = None) -> GoldenResult:
    """Compare computed `pvs` (trade_id -> pv) to the golden baseline."""
    baseline = baseline or GoldenBaseline.load()
    breaks: list[GoldenBreak] = []
    missing: list[str] = []
    for tid, expected in baseline.pvs.items():
        if tid not in pvs:
            missing.append(tid)
            continue
        actual = pvs[tid]
        if abs(actual - expected) > baseline.abs_tol + baseline.rel_tol * abs(expected):
            breaks.append(GoldenBreak(tid, expected, actual))
    return GoldenResult(snapshot_id, snapshot_id == baseline.snapshot_id,
                        len(baseline.pvs), breaks, missing)
