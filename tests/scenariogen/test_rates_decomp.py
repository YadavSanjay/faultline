"""Tests for the F6 rate-decomposition drivers (real / breakeven / term-premium) and the F7
regime stamp on the catalog covariance (market-intelligence P5).

Unit tests are data-free; integration tests use the on-disk factor history (like the other
scenariogen real-data tests) and skip when the F6 feeds are absent.
"""

from __future__ import annotations

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.risk.factor_history import FactorHistory
from src.scenariogen import factor_keys as fk
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.config import load_plausibility_config
from src.scenariogen.covariance import covariance_from_history
from src.scenariogen.factor_vector import vector_to_transforms
from src.scenariogen.plausibility import PlausibilityChecker, factor_class


# --------------------------------------------------------------------------- #
# unit — taxonomy
# --------------------------------------------------------------------------- #
def test_is_rates_decomp_and_keys():
    for key in (fk.REAL_10Y_KEY, fk.BREAKEVEN_10Y_KEY, fk.TERM_PREMIUM_10Y_KEY):
        assert fk.is_rates_decomp(key)
    assert fk.REAL_10Y_KEY == "real:DFII10"
    assert fk.BREAKEVEN_10Y_KEY == "be:T10YIE"
    assert fk.TERM_PREMIUM_10Y_KEY == "tp:ACMTP10"
    assert not fk.is_rates_decomp("ir:DGS10")
    assert not fk.is_rates_decomp("eq:NVDA")
    assert not fk.is_ir(fk.REAL_10Y_KEY)          # a real yield is its own class, not nominal ir


def test_rates_decomp_maps_to_its_own_plausibility_class():
    assert factor_class("real:DFII10") == "rates_decomp"
    assert factor_class("be:T10YIE") == "rates_decomp"
    assert factor_class("tp:ACMTP10") == "rates_decomp"


def test_rates_decomp_are_driver_only_no_bump():
    # like style/basis:ETF, the decomposition drivers lower to NO transform (reprice via Σ)
    assert vector_to_transforms({"real:DFII10": 0.0075}) == []
    assert vector_to_transforms({"be:T10YIE": 0.005}) == []
    assert vector_to_transforms({"tp:ACMTP10": 0.004}) == []


# --------------------------------------------------------------------------- #
# integration — needs data/marketdata (fred_real_rates.csv, nyfed_acm_term_premium.csv)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def history() -> FactorHistory:
    return FactorHistory(get_project_root(), load_pricer_config())


def _has_f6(history: FactorHistory) -> bool:
    real = history._read("rates/fred_real_rates.csv")
    acm = history._read("rates/nyfed_acm_term_premium.csv")
    return real is not None and acm is not None


def test_include_style_toggles_the_decomposition_columns(history):
    if not _has_f6(history):
        pytest.skip("F6 feeds not present")
    base = history.aligned_panel(250, include_style=False)
    styled = history.aligned_panel(250, include_style=True)
    for key in ("real:DFII10", "be:T10YIE", "tp:ACMTP10"):
        assert key not in base.columns          # full-reval path stays nominal-only
        assert key in styled.columns


def test_real_rate_shock_sinks_tech_and_long_bonds_together(history):
    """FINDING 6: a real-rate-driven repricing propagates to nominal rates UP and long-duration
    tech DOWN at once -- the 2022 co-move a nominal-only factor set cannot express."""
    if not _has_f6(history):
        pytest.skip("F6 feeds not present")
    cov = covariance_from_history(history, lookback=250, window="ewma", shrinkage=0.5,
                                  include_style=True)
    assert "real:DFII10" in cov.factors
    prop = cov.conditional_mean({"real:DFII10": 0.0075})     # +75bp real-rate shock
    assert prop["ir:DGS10"] > 0                              # nominal 10Y rises with the real rate
    # long-duration tech falls under the higher discount rate (at least the semis complex)
    assert prop["eq:NVDA"] < 0 and prop["eq:SMH"] < 0


def test_inflation_repricing_replay_is_promotable(history):
    """The 2022 real-rate repricing replay (which replaced the authored real_rate_repricing)."""
    if not _has_f6(history):
        pytest.skip("F6 feeds not present")
    cat = ScenarioCatalog.from_config(history)
    if "inflation_repricing" not in cat.replays:
        pytest.skip("inflation_repricing replay not applied")
    ms = cat.materialize("inflation_repricing", "mild")       # the episode as observed
    assert ms.vector["real:DFII10"] > 0                      # real yields rose (Aug-Oct 2022)
    assert ms.vector["ir:DGS10"] > 0
    res = PlausibilityChecker(cat.covariance, load_plausibility_config(get_project_root())).check(ms)
    assert res.promotable


def test_catalog_stamps_the_f7_regime_on_the_covariance(history):
    """F7: the catalog resolves the market-implied Σ regime and stamps it for provenance."""
    if not _has_f6(history):
        pytest.skip("F6 feeds not present")
    cat = ScenarioCatalog.from_config(history)
    regime = cat.covariance.as_dict().get("regime")
    if regime is None:
        pytest.skip("implied_corr_regime disabled")
    assert regime["source"] in ("COR3M", "COR1M", "DSPX")
    assert 0.0 <= regime["percentile"] <= 1.0
    assert regime["regime"] in ("systemic", "normal", "idiosyncratic")
    # the stamped shrinkage is the one that actually built Σ (regime, not the fixed config value)
    assert regime["shrinkage"] == pytest.approx(
        cat.covariance.as_dict()["regime"]["shrinkage"])
