"""Tests for the F4 style-factor driver (Ken French style:MOM in the scenariogen covariance).

The unit tests are data-free; the integration tests use the on-disk factor history (like the other
scenariogen real-data tests) to confirm the MOM factor enters Σ and propagates a momentum crash to
the high-momentum names.
"""

from __future__ import annotations

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.risk.factor_history import FactorHistory
from src.scenariogen import factor_keys as fk
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.config import load_plausibility_config
from src.scenariogen.covariance import covariance_from_history
from src.scenariogen.plausibility import PlausibilityChecker, factor_class


# --------------------------------------------------------------------------- #
# unit — taxonomy
# --------------------------------------------------------------------------- #
def test_is_style_and_key():
    assert fk.is_style("style:MOM")
    assert fk.STYLE_MOM_KEY == "style:MOM"
    assert not fk.is_style("eq:NVDA")
    assert not fk.is_equity("style:MOM")


def test_is_basis_and_key():
    assert fk.is_basis("basis:SMH_SEMI")
    assert fk.BASIS_ETF_KEY == "basis:SMH_SEMI"
    assert not fk.is_basis("eq:SMH")
    assert not fk.is_equity("basis:SMH_SEMI")


def test_style_and_basis_map_to_their_own_plausibility_classes():
    assert factor_class("style:MOM") == "style"
    assert factor_class("basis:SMH_SEMI") == "basis"
    # the two spread bases are bp-quoted, so they take a distinct bp bound class
    assert factor_class("basis:SWAP_SPREAD") == "basis_bp"
    assert factor_class("basis:CDS_BOND") == "basis_bp"


def test_spread_bases_lower_to_their_direct_bumps_but_etf_basis_does_not():
    from src.scenariogen.factor_vector import vector_to_transforms
    # the ETF dispersion basis is driver-only: it lowers to NO transform (reprices via equities)
    assert vector_to_transforms({"basis:SMH_SEMI": 0.08}) == []
    # the two spread bases lower to one direct bump each
    assert len(vector_to_transforms({"basis:SWAP_SPREAD": 0.003})) == 1
    assert len(vector_to_transforms({"basis:CDS_BOND": 0.006})) == 1


# --------------------------------------------------------------------------- #
# integration — needs data/marketdata (factors/ff_factors.csv, factor feeds)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def history() -> FactorHistory:
    return FactorHistory(get_project_root(), load_pricer_config())


@pytest.mark.needs_market_data
def test_include_style_toggles_the_mom_column(history):
    base = history.aligned_panel(250, include_style=False)
    styled = history.aligned_panel(250, include_style=True)
    if "mom" not in _has_ff(history):
        pytest.skip("ff_factors.csv not present")
    assert "style:MOM" not in base.columns          # full-reval path is unchanged
    assert "style:MOM" in styled.columns


def test_mom_is_in_sigma_and_positively_correlated_with_momentum_names(history):
    if "mom" not in _has_ff(history):
        pytest.skip("ff_factors.csv not present")
    cov = covariance_from_history(history, lookback=250, window="ewma", shrinkage=0.5,
                                  include_style=True)
    assert "style:MOM" in cov.factors
    corr = cov.correlation()
    mi = cov.factors.index("style:MOM")
    # the book's high-momentum names co-move positively with the momentum factor
    for tkr in ("eq:MU", "eq:XLK", "eq:SPX"):
        assert corr[mi, cov.factors.index(tkr)] > 0.0


def test_momentum_crash_propagates_to_equity_losses_and_passes_the_gate(history):
    if "mom" not in _has_ff(history):
        pytest.skip("ff_factors.csv not present")
    cat = ScenarioCatalog.from_config(history)
    if "momentum_crash" not in cat.sensitivity_names():
        pytest.skip("momentum_crash scenario not applied")
    ms = cat.materialize("momentum_crash", "adverse")
    assert ms.vector["style:MOM"] < 0                       # the driver is a negative MOM shock
    # high-momentum names fall, and harder than the broad index
    assert ms.vector["eq:MU"] < ms.vector["eq:SPX"] < 0
    # the scenario is promotable (the style bound admits a severe momentum crash)
    res = PlausibilityChecker(cat.covariance, load_plausibility_config(get_project_root())).check(ms)
    assert res.promotable


@pytest.mark.needs_market_data
def test_basis_factor_propagates_a_semis_dispersion(history):
    """F3: a basis:SMH_SEMI driver pulls the single-name semis apart from the SMH ETF -- the
    hedge failing that a name-level scenario nets to ~flat (the P2 exit criterion, trade #6)."""
    cov = covariance_from_history(history, lookback=250, window="ewma", shrinkage=0.5,
                                  include_style=True)
    assert "basis:SMH_SEMI" in cov.factors
    prop = cov.conditional_mean({"basis:SMH_SEMI": 0.08})
    # the single-name semis fall while the ETF holds -> the two legs diverge (no lockstep)
    assert prop["eq:MU"] < 0 and prop["eq:NVDA"] < 0
    assert prop["eq:SMH"] > prop["eq:MU"]                  # SMH holds up relative to the basket
    assert prop["eq:MU"] < prop["eq:SPX"]                  # dispersion is a semis story, not a market one


@pytest.mark.needs_market_data
def test_semis_dispersion_scenario_is_promotable(history):
    cat = ScenarioCatalog.from_config(history)
    if "semis_dispersion" not in cat.sensitivity_names():
        pytest.skip("semis_dispersion scenario not applied")
    ms = cat.materialize("semis_dispersion", "adverse")
    assert ms.vector["basis:SMH_SEMI"] > 0
    assert ms.vector["eq:MU"] < ms.vector["eq:SMH"]        # long semis fall, short SMH holds
    res = PlausibilityChecker(cat.covariance, load_plausibility_config(get_project_root())).check(ms)
    assert res.promotable


@pytest.mark.needs_market_data
def test_funding_basis_squeeze_dislocates_the_spread_bases_and_is_promotable(history):
    """F3 follow-on: the swap-spread and CDS-bond bases are authorable overlays that reprice
    directly (they have no Σ history), on top of a covariance credit driver."""
    cat = ScenarioCatalog.from_config(history)
    if "funding_basis_squeeze" not in cat.sensitivity_names():
        pytest.skip("funding_basis_squeeze scenario not applied")
    ms = cat.materialize("funding_basis_squeeze", "adverse")
    assert ms.vector["basis:SWAP_SPREAD"] > 0 and ms.vector["basis:CDS_BOND"] > 0
    assert ms.vector["credit"] > 0                          # the covariance driver
    res = PlausibilityChecker(cat.covariance, load_plausibility_config(get_project_root())).check(ms)
    assert res.promotable


def _has_ff(history: FactorHistory) -> set[str]:
    df = history._read("factors/ff_factors.csv")
    return set(df.columns) if df is not None else set()
