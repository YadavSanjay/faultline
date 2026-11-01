"""P5 tests: config validation, per-environment overlay, runtime settings."""

from __future__ import annotations

import json

import pytest
from src.pricer.config import _deep_merge, load_pricer_config
from src.pricer.config_models import ConfigError, validate_config
from src.pricer.settings import Settings


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_real_config_validates():
    cfg = load_pricer_config()                     # must not raise
    assert cfg["max_data_staleness_days"] == 7
    assert "risk" in cfg and "observability" in cfg


def test_validate_rejects_bad_type():
    with pytest.raises(ConfigError):
        validate_config({"max_data_staleness_days": "seven"})


def test_validate_rejects_bad_action():
    with pytest.raises(ConfigError) as exc:
        validate_config({"data_quality": {"default_action": "explode"}})
    assert "default_action" in str(exc.value)


def test_validate_rejects_bad_kind_action():
    with pytest.raises(ConfigError):
        validate_config({"data_quality": {"kinds": {"ois_curve": {"action": "nope"}}}})


def test_validate_passes_through_extra_keys():
    cfg = validate_config({"max_data_staleness_days": 5, "rates": {"weird_leaf": 1}})
    assert cfg["rates"]["weird_leaf"] == 1         # unchanged; extra allowed


# --------------------------------------------------------------------------- #
# Environment overlay
# --------------------------------------------------------------------------- #
def test_deep_merge():
    base = {"a": 1, "risk": {"x": 1, "y": 2}}
    over = {"risk": {"y": 20, "z": 3}}
    assert _deep_merge(base, over) == {"a": 1, "risk": {"x": 1, "y": 20, "z": 3}}


def test_env_overlay(tmp_path, monkeypatch):
    base = tmp_path / "pricer_config.json"
    base.write_text(json.dumps({"max_data_staleness_days": 7, "risk": {"ir_bump_bp": 1.0}}))
    (tmp_path / "pricer_config.uat.json").write_text(
        json.dumps({"max_data_staleness_days": 3, "risk": {"ir_bump_bp": 2.0}}))
    monkeypatch.setattr("src.pricer.config._DEFAULT_CONFIG", base)
    monkeypatch.setenv("PRICER_ENV", "uat")

    cfg = load_pricer_config()
    assert cfg["max_data_staleness_days"] == 3     # overlay wins
    assert cfg["risk"]["ir_bump_bp"] == 2.0


# --------------------------------------------------------------------------- #
# Runtime settings
# --------------------------------------------------------------------------- #
def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("PRICER_ENV", "prod")
    monkeypatch.setenv("PRICER_JSON_LOGS", "true")
    monkeypatch.setenv("PRICER_WORKERS", "4")
    s = Settings()
    assert s.env == "prod" and s.json_logs is True and s.workers == 4


def test_settings_secret_is_masked(monkeypatch):
    # PRICER_API_KEY is the pricer's one secret; market-data source keys (FRED_API_KEY etc.) are
    # un-prefixed and live in src/marketdata/common/settings.py, tested there.
    monkeypatch.setenv("PRICER_API_KEY", "supersecret")
    s = Settings()
    assert s.api_key is not None
    assert "supersecret" not in str(s) and "supersecret" not in repr(s)
    assert s.api_key.get_secret_value() == "supersecret"
