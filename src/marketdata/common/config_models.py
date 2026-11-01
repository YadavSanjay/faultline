"""Typed validation for the market-data configs.

A lenient pydantic schema over `configs/marketdata/<asset_class>/<asset_class>_marketdata.json`
so a mis-typed source field (e.g. `timeout_s` as a string, `enabled` as a number) or a missing
`asset_class` fails fast at load time with a clear `ConfigError`, instead of an obscure
AttributeError deep inside a downloader. Only the fields the downloaders rely on are typed;
everything else (portfolio, time_window, endpoint templates, per-source extras) passes through
via `extra=allow`.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from src.common.config.models import ConfigError, Lenient, validate

__all__ = ["AssetClassConfig", "ConfigError", "SourceConfig", "validate_marketdata_config"]


class SourceConfig(Lenient):
    enabled: bool = True
    name: str | None = None
    base_url: str | None = None
    auth: dict[str, Any] | None = None          # e.g. {"type": "none"} or oauth2 block
    timeout_s: float | None = None              # int in JSON coerces to float
    rate_limit_delay_s: float | None = None
    output_file: str | None = None              # some sources use `output_files` (dict) instead


class AssetClassConfig(Lenient):
    asset_class: str
    sources: dict[str, SourceConfig] = Field(default_factory=dict)


def validate_marketdata_config(raw: dict[str, Any], asset_class: str) -> dict[str, Any]:
    """Validate `raw` against AssetClassConfig; return it unchanged. Raise ConfigError on failure."""
    return validate(AssetClassConfig, raw, f"{asset_class} market-data config")
