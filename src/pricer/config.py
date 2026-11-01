"""Pricer configuration + project-root resolution (validated, env-aware — P5).

`load_pricer_config()` loads the governed model-parameter file, deep-merges an optional
per-environment overlay (`pricer_config.<PRICER_ENV>.json`), and validates the result —
failing fast with a clear `ConfigError` on a mis-typed or mis-spelled setting. It returns
a plain dict (validation is a gate, not a transform), so downstream `config.get(...)`
access and the provenance config-hash are unchanged.
"""

from __future__ import annotations

from pathlib import Path

from src.common.config.loader import current_env as _current_env_for
from src.common.config.loader import deep_merge as _deep_merge
from src.common.config.loader import load_json as _load_json
from src.common.storage import repo_root as _repo_root

from .config_models import validate_config

# Single source of truth for the repo root (was: Path(__file__).parents[2]).
PROJECT_ROOT = _repo_root()

_DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "pricer" / "pricer_config.json"


def get_project_root() -> Path:
    return PROJECT_ROOT


def _current_env() -> str:
    return _current_env_for("PRICER")


def load_pricer_config(path: Path | None = None) -> dict:
    """Load + validate the pricer config.

    With the default config (path is None), a per-environment overlay
    `pricer_config.<PRICER_ENV>.json` next to it — if present — is deep-merged on top, so
    governed model params stay in the base file and per-env tweaks live in the overlay.
    """
    explicit = path is not None
    path = path or _DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"Pricer config not found: {path}")
    raw = _load_json(path)
    if not explicit:
        overlay = path.with_name(f"pricer_config.{_current_env()}.json")
        if overlay.exists():
            raw = _deep_merge(raw, _load_json(overlay))
    return validate_config(raw)
