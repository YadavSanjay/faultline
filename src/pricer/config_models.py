"""Typed validation for the pricer config (P5).

A pydantic model over `pricer_config.json` that fails fast with a clear message on a
mis-typed or mis-spelled setting. The cross-cutting fragments (`Lenient`, observability,
data-quality, `ConfigError`, the `validate` gate) now live in `src/common/config/models.py`;
this module declares only the pricer-specific sections. Every model allows `extra` keys so
the rich per-asset leaf config passes through unmodeled.

Validation is a *gate*: `validate_config(raw)` returns the original dict unchanged.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError, model_validator

from src.common.config.models import (
    ConfigError,
    DataQualityConfig,
    Lenient,
    ObservabilityConfig,
    validate,
)

__all__ = ["ConfigError", "PricerConfig", "RiskConfig", "risk_config", "validate_config"]


class VarConfig(Lenient):
    confidence_levels: list[float] = Field(default_factory=lambda: [0.95, 0.975, 0.99])
    horizon_days: int = 1
    lookback_days: int = 250


class RiskConfig(Lenient):
    ir_bump_bp: float = 1.0
    credit_bump_bp: float = 1.0
    rates_vol_bump_bp: float = 1.0
    equity_vol_bump: float = 0.01
    spot_bump_pct: float = 1.0
    fx_bump_pct: float = 1.0
    scenarios: dict[str, Any] = Field(default_factory=dict)
    var: VarConfig = Field(default_factory=VarConfig)
    # Aligned factor-move panel (factor_history.carry_short_gaps): carry a factor's level across an
    # interior gap of at most this many eligible rows; 0 restores the plain all-factors inner join.
    panel_max_gap_days: int = Field(default=3, ge=0, strict=True)
    # A date stays in the panel only when at least this share of factors observed it.
    panel_min_coverage: float = Field(default=0.75, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _no_unknown_panel_keys(self) -> RiskConfig:
        # The model is lenient, so a misspelt panel key would otherwise fall back to the default.
        unknown = sorted(k for k in (self.model_extra or {}) if k.startswith("panel_"))
        if unknown:
            raise ValueError(f"unknown risk panel setting(s) {unknown}; "
                             "expected panel_max_gap_days / panel_min_coverage")
        return self


class PricerConfig(Lenient):
    """Top-level pricer config. Asset sections (rates/bonds/options/credit/fx) and any
    other keys pass through via `extra=allow`; only cross-cutting settings are typed."""

    max_data_staleness_days: int = 7
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    data_quality: DataQualityConfig | None = None
    risk: RiskConfig | None = None


def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate `raw` against PricerConfig; return it unchanged. Raise ConfigError on failure."""
    return validate(PricerConfig, raw, "pricer config")


def risk_config(raw: dict[str, Any]) -> RiskConfig:
    """The validated, defaulted `risk` section of a pricer config dict (missing section = defaults).

    Raises:
        ConfigError: If the `risk` section fails validation.
    """
    try:
        return RiskConfig.model_validate(raw.get("risk") or {})
    except ValidationError as exc:
        raise ConfigError(f"Invalid pricer config risk section:\n{exc}") from exc
