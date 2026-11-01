"""marketdata config validation + credentials (item 2/6)."""

from __future__ import annotations

import pytest
from src.common.config.models import ConfigError
from src.marketdata.common.config_loader import load_config
from src.marketdata.common.config_models import validate_marketdata_config
from src.marketdata.common.settings import load_credentials


@pytest.mark.parametrize("asset_class",
                         ["bonds", "credit", "equity", "fx", "options", "rates", "reference", "stress"])
def test_real_configs_load_and_validate(asset_class: str):
    cfg = load_config(asset_class)
    assert cfg["asset_class"]
    assert isinstance(cfg["sources"], dict) and cfg["sources"]


def test_bad_source_field_raises_naming_field():
    with pytest.raises(ConfigError) as exc:
        validate_marketdata_config(
            {"asset_class": "x", "sources": {"s": {"enabled": "not-a-bool"}}}, "x")
    assert "enabled" in str(exc.value)


def test_missing_asset_class_raises():
    with pytest.raises(ConfigError):
        validate_marketdata_config({"sources": {}}, "x")


def test_load_credentials_returns_string_dict():
    creds = load_credentials()
    assert {"FRED_API_KEY", "STOOQ_API_KEY", "EIA_API_KEY", "OPENFIGI_API_KEY"} <= set(creds)
    assert all(isinstance(v, str) for v in creds.values())
