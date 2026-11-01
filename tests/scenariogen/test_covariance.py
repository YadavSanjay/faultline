"""S0 covariance tests: known co-moves, window policies, content-addressed cov_id."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.risk.factor_history import FactorHistory
from src.scenariogen.covariance import build_covariance, covariance_from_history


def _synthetic_panel(n: int = 120) -> pd.DataFrame:
    """a is the systematic factor; b=2a, c=-0.5a (exact co-moves); d is constant."""
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(0)
    a = rng.normal(0.0, 0.01, n)
    return pd.DataFrame({"a": a, "b": 2.0 * a, "c": -0.5 * a, "d": np.full(n, 0.01)}, index=idx)


# --------------------------------------------------------------------------- #
# Conditional-covariance propagation vs known co-moves (the core S1 correctness)
# --------------------------------------------------------------------------- #
def test_conditional_mean_recovers_known_comoves():
    cov = build_covariance(_synthetic_panel(), window="full")
    out = cov.conditional_mean({"a": 0.10})
    assert out["a"] == pytest.approx(0.10)            # driver held at asserted value
    assert out["b"] == pytest.approx(0.20, abs=1e-9)  # b = 2a
    assert out["c"] == pytest.approx(-0.05, abs=1e-9)  # c = -0.5a
    assert out["d"] == pytest.approx(0.01, abs=1e-9)  # constant -> its mean


def test_conditional_mean_rejects_unknown_and_empty_drivers():
    cov = build_covariance(_synthetic_panel(), window="full")
    with pytest.raises(KeyError):
        cov.conditional_mean({"zzz": 0.1})
    with pytest.raises(ValueError):
        cov.conditional_mean({})


def test_correlation_matrix_signs():
    cov = build_covariance(_synthetic_panel(), window="full")
    corr = cov.correlation()
    ia, ib, ic = cov.index("a"), cov.index("b"), cov.index("c")
    assert corr[ia, ia] == pytest.approx(1.0)
    assert corr[ia, ib] == pytest.approx(1.0, abs=1e-9)    # perfectly positively co-moving
    assert corr[ia, ic] == pytest.approx(-1.0, abs=1e-9)   # perfectly negatively co-moving


# --------------------------------------------------------------------------- #
# Window policies
# --------------------------------------------------------------------------- #
def test_ewma_weights_recent_days_more():
    n = 120
    idx = pd.bdate_range("2020-01-01", periods=n)
    x = np.concatenate([np.zeros(n // 2), np.ones(n // 2)])   # regime shift to 1 recently
    panel = pd.DataFrame({"f": x, "g": x}, index=idx)
    full = build_covariance(panel, window="full")
    ewma = build_covariance(panel, window="ewma", ewma_lambda=0.9)
    assert full.mean[full.index("f")] == pytest.approx(0.5)
    assert ewma.mean[ewma.index("f")] > full.mean[full.index("f")]   # recent 1s dominate


def test_crisis_window_selects_subset():
    panel = _synthetic_panel(40)        # Jan-Feb 2020 business days
    cov = build_covariance(panel, window="crisis",
                           crisis_windows=[("2020-02-01", "2020-02-29")])
    assert cov.window == "crisis"
    assert 2 <= cov.n_obs < len(panel)


def test_crisis_window_requires_ranges():
    with pytest.raises(ValueError):
        build_covariance(_synthetic_panel(), window="crisis")


def test_unknown_window_rejected():
    with pytest.raises(ValueError):
        build_covariance(_synthetic_panel(), window="bogus")


# --------------------------------------------------------------------------- #
# Reproducibility: content-addressed cov_id
# --------------------------------------------------------------------------- #
def test_cov_id_is_deterministic_and_distinguishes_windows():
    panel = _synthetic_panel()
    a1 = build_covariance(panel, window="full")
    a2 = build_covariance(panel, window="full")
    assert a1.cov_id == a2.cov_id and len(a1.cov_id) == 16
    assert build_covariance(panel, window="ewma").cov_id != a1.cov_id


# --------------------------------------------------------------------------- #
# Integration against the real aligned factor panel
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_covariance_from_real_history():
    fh = FactorHistory(get_project_root(), load_pricer_config())
    cov = covariance_from_history(fh, lookback=60, window="ewma")
    assert cov.n_obs > 10
    assert cov.cov.shape == (len(cov.factors), len(cov.factors))
    assert any(f.startswith("eq:") for f in cov.factors)
    assert any(f.startswith("ir:") for f in cov.factors)
    # conditioning on a real driver yields a complete vector over all factors
    driver = next(f for f in cov.factors if f.startswith("eq:"))
    out = cov.conditional_mean({driver: -0.10})
    assert set(out) == set(cov.factors)
