"""Shared pydantic config fragments + the validation gate.

`Lenient` allows unknown keys so rich per-module leaf config passes through unmodeled —
only the fields code depends on are type-checked. `validate(model, raw, label)` runs a model
as a *gate*: it returns `raw` unchanged on success (so downstream `.get(...)` access and the
provenance config-hash are unaffected) and raises `ConfigError` with a clear message on
failure. `ObservabilityConfig` / `DataQualityConfig` live here because more than one module
(pricer, the post-ingest data-quality gate) needs them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_ACTIONS = {"warn", "block", "substitute"}


class ConfigError(ValueError):
    """Raised when a config fails schema validation."""


class Lenient(BaseModel):
    """Base model that tolerates unknown keys (only declared fields are type-checked)."""

    model_config = ConfigDict(extra="allow")


class PvDriftConfig(Lenient):
    abs_tol: float = 1000.0
    rel_tol: float = 0.02


class ObservabilityConfig(Lenient):
    json_logs: bool = False
    metrics_textfile: str = "logs/metrics/pricer.prom"
    pv_drift: PvDriftConfig = Field(default_factory=PvDriftConfig)


class DataQualityKind(Lenient):
    action: str | None = None
    max_staleness_days: int | None = None

    @field_validator("action")
    @classmethod
    def _action_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in _ACTIONS:
            raise ValueError(f"action must be one of {sorted(_ACTIONS)}, got {v!r}")
        return v


class DataQualityConfig(Lenient):
    default_action: str = "warn"
    kinds: dict[str, DataQualityKind] = Field(default_factory=dict)

    @field_validator("default_action")
    @classmethod
    def _default_valid(cls, v: str) -> str:
        if v not in _ACTIONS:
            raise ValueError(f"default_action must be one of {sorted(_ACTIONS)}, got {v!r}")
        return v


def validate(model: type[BaseModel], raw: dict[str, Any], label: str) -> dict[str, Any]:
    """Validate `raw` against `model` and return it unchanged; raise ConfigError on failure."""
    try:
        model.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid {label}:\n{exc}") from exc
    return raw
