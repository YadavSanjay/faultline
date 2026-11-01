"""Tests for the F7 implied-correlation Σ regime switch (market-intelligence P5).

Offline: the regime is driven with in-memory series, so nothing touches data/. The core
properties are the correlation->shrinkage mapping direction, the DSPX sign inversion, and the
byte-identical disabled fallback.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.scenariogen.implied_corr import ImpliedCorrelationRegime, resolve_regime


def _regime(values: list[float], source: str = "COR3M", **cfg) -> ImpliedCorrelationRegime:
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return ImpliedCorrelationRegime(
        source=source, series=pd.Series(values, index=idx),
        shrink_systemic=float(cfg.get("shrink_systemic", 0.4)),
        shrink_idiosyncratic=float(cfg.get("shrink_idiosyncratic", 0.65)),
        systemic_pct=float(cfg.get("systemic_pct", 0.66)),
        idiosyncratic_pct=float(cfg.get("idiosyncratic_pct", 0.33)),
    )


def test_elevated_correlation_lowers_shrinkage_systemic():
    # latest value is the max -> percentile 1.0 -> systemic -> shrink_systemic (low)
    st = _regime([10, 20, 30, 40, 100]).resolve()
    assert st.percentile == pytest.approx(1.0)
    assert st.regime == "systemic"
    assert st.shrinkage == pytest.approx(0.4)


def test_low_correlation_raises_shrinkage_idiosyncratic():
    # latest value is the min -> percentile 0.2 (1 of 5 <= it) -> idiosyncratic -> high shrinkage
    st = _regime([100, 80, 60, 40, 10]).resolve()
    assert st.percentile == pytest.approx(0.2)
    assert st.regime == "idiosyncratic"
    assert st.shrinkage == pytest.approx(0.65 - 0.2 * (0.65 - 0.4))   # 0.60


def test_shrinkage_is_linear_and_monotonic_in_percentile():
    lo = _regime([100, 80, 60, 40, 10]).resolve()      # pct 0.2
    hi = _regime([10, 20, 30, 40, 100]).resolve()      # pct 1.0
    assert hi.shrinkage < lo.shrinkage                  # more correlation -> less shrinkage
    # linear: shrinkage = idio - pct*(idio - sys)
    assert hi.shrinkage == pytest.approx(0.65 - 1.0 * (0.65 - 0.4))
    assert lo.shrinkage == pytest.approx(0.65 - 0.2 * (0.65 - 0.4))


def test_dispersion_source_is_inverted():
    # DSPX is dispersion: a HIGH dispersion reading = LOW correlation -> should read idiosyncratic
    st = _regime([10, 20, 30, 40, 100], source="DSPX").resolve()
    assert st.percentile == pytest.approx(0.0)          # 1 - 1.0
    assert st.regime == "idiosyncratic"
    assert st.shrinkage == pytest.approx(0.65)


def test_disabled_returns_fixed_shrinkage_and_not_enabled(tmp_path):
    st = resolve_regime(tmp_path, {"enabled": False}, fixed_shrinkage=0.5)
    assert st.enabled is False
    assert st.shrinkage == pytest.approx(0.5)
    assert st.regime == "fixed"


def test_absent_series_falls_back_to_fixed(tmp_path):
    # enabled but no data on disk -> disabled state carrying the fixed shrinkage
    st = resolve_regime(tmp_path, {"enabled": True, "source": "COR3M"}, fixed_shrinkage=0.5)
    assert st.enabled is False
    assert st.shrinkage == pytest.approx(0.5)


def test_regime_thresholds_label_normal_band():
    # 5 values, latest 25 -> 3 of 5 <= it -> percentile 0.6, between thresholds 0.33/0.66 -> normal
    st = _regime([10, 20, 30, 40, 25]).resolve()
    assert 0.33 < st.percentile < 0.66
    assert st.regime == "normal"
