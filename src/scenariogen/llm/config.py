"""scenariogen LLM-flow configuration loading (P0).

Config lives under ``configs/scenariogen/llm/`` (namespaced beneath the existing scenariogen
config dir). Three plain-JSON files, loaded in the same spirit as ``src/scenariogen/config.py``:

  llm.json       providers, model registry, per-stage provider profiles, per-stage params, retry
                 policy, budgets — validated by `profiles.load_llm_config` (a mistyped key raises)
  mcp.json       ARag + MacroRAG MCP server launch specs + per-phase tool allowlists (used in P1)
  pricing.json   the governed rate card; every model a profile uses must have a row (design D9)
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from ..config import config_dir
from .profiles import Budgets, LLMConfig, RetryPolicy, load_llm_config

# The flow, phases and runner take the validated config under its historical name.
LLMSettings = LLMConfig

__all__ = [
    "Budgets",
    "LLMSettings",
    "RetryPolicy",
    "llm_config_dir",
    "load_llm_settings",
    "load_mcp_settings",
    "load_pricing_overrides",
]


def llm_config_dir(root: Path | None = None) -> Path:
    return config_dir(root) / "llm"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"scenariogen LLM config not found: {path}")
    with open(path, encoding="utf-8") as fh:
        return cast("dict[str, Any]", json.load(fh))


def load_llm_settings(
    root: Path | None = None, *, profile: str | None = None, environ: Mapping[str, str] | None = None
) -> LLMConfig:
    """Loads and validates ``llm.json`` with ``pricing.json``, resolving the active profile.

    Args:
        root: Project root; the default project root when None.
        profile: Overrides the configured ``active_profile`` (the runner's ``--llm-profile``).
        environ: Environment for ``${SCENARIOGEN_LLM_PROFILE:-openai}``; ``os.environ`` when None.

    Raises:
        ConfigError: Either file is invalid, a profile model is unpriced, or the profile is unknown.
        FileNotFoundError: A config file is missing.
    """
    directory = llm_config_dir(root)
    return load_llm_config(_load_json(directory / "llm.json"), _load_json(directory / "pricing.json"),
                           profile=profile, environ=environ)


def load_mcp_settings(root: Path | None = None) -> dict[str, Any]:
    # Expand ${VAR}/${VAR:-default} so a server's location (repo path, python, inference URL)
    # is env-overridable in one place (harness-audit F6).
    from src.common.config.env import expand_env_vars

    return cast("dict[str, Any]", expand_env_vars(_load_json(llm_config_dir(root) / "mcp.json")))


def load_pricing_overrides(root: Path | None = None) -> dict[str, Any] | None:
    """The raw ``pricing.json`` rate card, or None when the file is absent."""
    path = llm_config_dir(root) / "pricing.json"
    if not path.exists():
        return None
    return _load_json(path)
