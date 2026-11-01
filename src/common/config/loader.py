"""Config-file plumbing shared by every module: load, deep-merge, env, date resolution.

These are the primitives each module composes into its own `load_*_config()`:
JSON loading, recursive overlay merge (base + per-environment file), the current
environment name, and the `"today"` placeholder resolution the market-data configs use.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, cast


def load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return cast("dict[str, Any]", json.load(fh))


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` onto `base` (dicts merged, other values replaced)."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def current_env(prefix: str = "PRICER") -> str:
    """The active environment name from `<PREFIX>_ENV` (default 'dev')."""
    return os.environ.get(f"{prefix}_ENV", "dev").strip().lower()


def resolve_dates(obj: Any, today: str | None = None) -> Any:
    """Recursively replace the string "today" with an ISO date (injectable for tests)."""
    today = today or date.today().isoformat()
    if isinstance(obj, dict):
        return {k: resolve_dates(v, today) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_dates(v, today) for v in obj]
    if isinstance(obj, str) and obj.strip().lower() == "today":
        return today
    return obj
