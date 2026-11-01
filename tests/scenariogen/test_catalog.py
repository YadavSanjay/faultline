"""S2 catalog tests: named scenarios resolve at severity tiers from config."""

from __future__ import annotations

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.risk.factor_history import FactorHistory
from src.scenariogen.catalog import ScenarioCatalog

pytestmark = pytest.mark.needs_market_data


def _catalog() -> ScenarioCatalog:
    fh = FactorHistory(get_project_root(), load_pricer_config())
    return ScenarioCatalog.from_config(fh, lookback=60)


def test_catalog_loads_names_and_tiers():
    cat = _catalog()
    assert "taiwan_geopolitical" in cat.names()
    assert set(cat.tiers()) == {"mild", "adverse", "severe"}
    assert cat.covariance.n_obs > 10


def test_materialize_named_scenario_full_vector():
    cat = _catalog()
    ms = cat.materialize("taiwan_geopolitical", "adverse")
    assert ms.severity == "adverse"
    assert set(cat.covariance.factors) <= set(ms.vector)   # complete, propagated vector
    assert ms.transforms()


def test_severity_tiers_scale_monotonically():
    cat = _catalog()
    mild = cat.materialize("keyman_nvda", "mild")
    severe = cat.materialize("keyman_nvda", "severe")
    # NVDA is a driver -> a more severe tier means a strictly larger sell-off
    assert severe.vector["eq:NVDA"] < mild.vector["eq:NVDA"] < 0.0


def test_materialize_all_covers_grid():
    cat = _catalog()
    allms = cat.materialize_all()
    assert len(allms) == len(cat.names()) * len(cat.tiers())


def test_unknown_scenario_or_tier_raises():
    cat = _catalog()
    with pytest.raises(KeyError):
        cat.materialize("does_not_exist", "adverse")
    with pytest.raises(KeyError):
        cat.materialize("taiwan_geopolitical", "apocalyptic")
