"""Governance report (P4): bundle the golden gate, IPV, and inventory coverage."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.common import storage

from .golden import GoldenResult
from .ipv import IpvReport


@dataclass
class GovernanceReport:
    valuation_date: str
    run_id: str
    snapshot_id: str
    golden: GoldenResult
    ipv: IpvReport
    inventory_covered: list[str] = field(default_factory=list)
    inventory_missing: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.golden.passed and self.ipv.all_passed and not self.inventory_missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "valuation_date": self.valuation_date,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "golden": self.golden.to_dict(),
            "ipv": self.ipv.to_dict(),
            "model_inventory": {
                "covered": self.inventory_covered,
                "missing": self.inventory_missing,
            },
        }


def write_governance_report(report: GovernanceReport, project_root: Path) -> Path | str:
    out = storage.join(
        storage.data_base(project_root), "pricer", "governance", report.valuation_date,
        f"governance_{report.run_id}.json")
    storage.write_text_atomic(out, json.dumps(report.to_dict(), indent=2, default=str))
    return out if storage.is_remote_uri(out) else Path(out)
