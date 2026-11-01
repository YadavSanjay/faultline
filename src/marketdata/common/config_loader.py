"""
Load and resolve market-data config files.

Config files live at:
    configs/marketdata/<asset_class>/<asset_class>_marketdata.json

Date placeholders resolved at load time:
    "today"  ->  current ISO date (YYYY-MM-DD)
"""

import logging
from pathlib import Path
from typing import cast

from src.common.config.loader import load_json, resolve_dates
from src.common.storage import repo_root as _repo_root
from src.marketdata.common.config_models import validate_marketdata_config

logger = logging.getLogger(__name__)

# Single source of truth for the repo root (was: Path(__file__).parents[3]).
_PROJECT_ROOT = _repo_root()


def load_config(asset_class: str) -> dict:
    """Load, validate, and date-resolve the config for the given asset class.

    Validation runs before date resolution so a mis-typed/mis-spelled field fails fast with a
    clear `ConfigError` naming it.
    """
    path = _PROJECT_ROOT / "configs" / "marketdata" / asset_class / f"{asset_class}_marketdata.json"
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    raw = validate_marketdata_config(load_json(path), asset_class)
    config = resolve_dates(raw)
    logger.info("Loaded config for asset_class=%r from %s", asset_class, path)
    return cast("dict", config)


def get_project_root() -> Path:
    return _PROJECT_ROOT
