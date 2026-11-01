"""Model inventory (P4) — governed registry of every pricer/model.

Loads configs/pricer/model_inventory.json and checks coverage: every trade-type the
portfolio actually prices must have a governed entry (model, engine, assumptions,
limitations, approved use, IPV controls). A missing entry is a governance gap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import get_project_root


@dataclass(frozen=True)
class ModelEntry:
    trade_type: str
    name: str
    asset_class: str
    model: str
    engine: str
    assumptions: list[str]
    limitations: list[str]
    approved_use: str
    ipv_controls: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"trade_type": self.trade_type, "name": self.name,
                "asset_class": self.asset_class, "model": self.model, "engine": self.engine,
                "assumptions": self.assumptions, "limitations": self.limitations,
                "approved_use": self.approved_use, "ipv_controls": self.ipv_controls}


@dataclass
class ModelInventory:
    entries: dict[str, ModelEntry]

    @classmethod
    def load(cls, path: Path | None = None) -> ModelInventory:
        path = path or get_project_root() / "configs" / "pricer" / "model_inventory.json"
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)["models"]
        entries = {
            tt: ModelEntry(
                trade_type=tt, name=e.get("name", ""), asset_class=e.get("asset_class", ""),
                model=e.get("model", ""), engine=e.get("engine", ""),
                assumptions=list(e.get("assumptions", [])),
                limitations=list(e.get("limitations", [])),
                approved_use=e.get("approved_use", ""),
                ipv_controls=list(e.get("ipv_controls", [])),
            )
            for tt, e in raw.items()
        }
        return cls(entries)

    def coverage(self, trade_types: list[str]) -> tuple[list[str], list[str]]:
        """Return (covered, missing) trade-types against this inventory."""
        present = set(self.entries)
        needed = sorted(set(trade_types))
        covered = [t for t in needed if t in present]
        missing = [t for t in needed if t not in present]
        return covered, missing

    def to_dict(self) -> dict[str, Any]:
        return {"n_models": len(self.entries),
                "models": {tt: e.as_dict() for tt, e in self.entries.items()}}
