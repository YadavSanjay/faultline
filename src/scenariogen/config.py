"""scenariogen configuration loading (S0/S1).

Config lives under ``configs/scenariogen/`` (project policy: scenario-generation
artifacts are namespaced separately from the pricer). Two files:

  covariance.json    covariance window policy (window/lookback/ewma_lambda/crisis_windows)
  scenarios.json     authored scenario definitions (drivers/method/overlays/severity tiers)
  plausibility.json  GATE bounds + historical-envelope policy (S3)
  macro.json         MacroRAG connection + per-scenario synthesis requests + GATE (S4)
  arag.json          ARag connection + per-scenario company Risk-Factor queries + GATE (dual-RAG)
  limits.json        loss/capital limits + reverse-stress solver settings (S5)

These are plain JSON loaders; governance/versioning of scenario definitions live in
versioning.py (S3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from src.pricer.config import get_project_root

_CONFIG_DIR = "scenariogen"


def config_dir(root: Path | None = None) -> Path:
    return (root or get_project_root()) / "configs" / _CONFIG_DIR


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"scenariogen config not found: {path}")
    with open(path, encoding="utf-8") as fh:
        return cast("dict[str, Any]", json.load(fh))


def load_covariance_config(root: Path | None = None) -> dict[str, Any]:
    return _load_json(config_dir(root) / "covariance.json")


def load_scenario_config(root: Path | None = None) -> dict[str, Any]:
    return _load_json(config_dir(root) / "scenarios.json")


def load_plausibility_config(root: Path | None = None) -> dict[str, Any]:
    return _load_json(config_dir(root) / "plausibility.json")


def _load_json_env(path: Path) -> dict[str, Any]:
    """Load a config and expand ${VAR}/${VAR:-default} so a service's LOCATION (base_url, host
    repo, inference URL) is env-overridable in one place (harness-audit F6)."""
    from src.common.config.env import expand_env_vars

    return cast("dict[str, Any]", expand_env_vars(_load_json(path)))


def load_macro_config(root: Path | None = None) -> dict[str, Any]:
    return _load_json_env(config_dir(root) / "macro.json")


def load_arag_config(root: Path | None = None) -> dict[str, Any]:
    return _load_json_env(config_dir(root) / "arag.json")


def load_limits_config(root: Path | None = None) -> dict[str, Any]:
    return _load_json(config_dir(root) / "limits.json")


def load_liquidity_config(root: Path | None = None) -> dict[str, Any]:
    """F1 exit-value policy: signed-off liquidity-horizon table + bid-ask half-spreads (P4)."""
    return _load_json(config_dir(root) / "liquidity.json")


def load_collateral_config(root: Path | None = None) -> dict[str, Any]:
    """F2 cash/collateral policy: margined-trade tag + IM proxy + funding gauge (P4)."""
    return _load_json(config_dir(root) / "collateral.json")


def load_news_config(root: Path | None = None) -> dict[str, Any]:
    """A3 news calibration: 8-K/GDELT -> clamped multiplier on the ARag idiosyncratic leg (P3)."""
    return _load_json(config_dir(root) / "news.json")


def load_geopolitical_config(root: Path | None = None) -> dict[str, Any]:
    """F8 GPR calibration: geopolitical severity multiplier + trigger for tagged scenarios (P7)."""
    return _load_json(config_dir(root) / "geopolitical.json")


def load_throughput_config(root: Path | None = None) -> dict[str, Any]:
    """F9 throughput calibration: AI-capex severity multiplier + thesis flag for ai_bust (P7)."""
    return _load_json(config_dir(root) / "throughput.json")


def load_crowding_config(root: Path | None = None) -> dict[str, Any]:
    """F5 crowding calibration: per-name days-to-cover multiplier on the ARag idiosyncratic leg (P7)."""
    return _load_json(config_dir(root) / "crowding.json")
