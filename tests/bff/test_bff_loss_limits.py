"""BFF stress-loss thresholds on the scenario catalog (`/scenarios` -> `limits`)."""

from __future__ import annotations

import json
from pathlib import Path

from ui.bff.app import scenarios


def _write_limits(root: Path, cfg: dict) -> None:
    d = root / "configs" / "scenariogen"
    d.mkdir(parents=True, exist_ok=True)
    (d / "limits.json").write_text(json.dumps(cfg), encoding="utf-8")


def test_catalog_carries_loss_limit_and_warning(bff_root: Path) -> None:
    _write_limits(bff_root, {"loss_limit": 120_000_000, "loss_warning": 80_000_000, "capital_limit": 1})
    limits = scenarios.catalog(None)["limits"]
    assert limits == {"loss_limit": 120_000_000.0, "loss_warning": 80_000_000.0}


def test_catalog_limits_null_when_unset_or_missing(bff_root: Path) -> None:
    assert scenarios.catalog(None)["limits"] == {"loss_limit": None, "loss_warning": None}
    _write_limits(bff_root, {"loss_limit": 120_000_000})
    assert scenarios.catalog(None)["limits"] == {"loss_limit": 120_000_000.0, "loss_warning": None}


def test_repo_limits_are_the_decided_thresholds() -> None:
    # configs/scenariogen/limits.json, set 2026-10-19: hard limit 12% and warning 8% of the
    # $1bn sizing capital_base; the warning must sit below the limit.
    cfg = json.loads((Path(__file__).resolve().parents[2] / "configs" / "scenariogen" / "limits.json")
                     .read_text(encoding="utf-8"))
    assert cfg["loss_limit"] == 120_000_000
    assert cfg["loss_warning"] == 80_000_000
    assert cfg["loss_warning"] < cfg["loss_limit"]
