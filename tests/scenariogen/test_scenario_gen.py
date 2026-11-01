"""S1 generator tests: unit parsing, definition loading, conditional materialization."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.risk.factor_history import FactorHistory
from src.scenariogen.config import load_covariance_config, load_scenario_config
from src.scenariogen.covariance import build_covariance, covariance_from_history
from src.scenariogen.scenario_gen import (
    METHOD_DRIVERS_ONLY,
    ScenarioDefinition,
    ScenarioGenerator,
    to_canonical,
)


def _panel(n: int = 120) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(1)
    a = rng.normal(0.0, 0.01, n)
    return pd.DataFrame({"eq:NVDA": a, "eq:TSM": 1.5 * a, "credit": -0.2 * a,
                         "ir:DGS10": np.full(n, 0.0)}, index=idx)


# --------------------------------------------------------------------------- #
# Unit parsing
# --------------------------------------------------------------------------- #
def test_to_canonical_units():
    assert to_canonical("ir:DGS10", {"move_bp": 100}) == pytest.approx(0.0100)
    assert to_canonical("eq:NVDA", {"move_pct": -25}) == pytest.approx(-0.25)
    assert to_canonical("eqvol", {"move_pts": 20}) == pytest.approx(0.20)
    assert to_canonical("ratesvol", {"move_rel": 0.3}) == pytest.approx(0.30)
    assert to_canonical("credit", {"move": 0.01}) == pytest.approx(0.01)
    assert to_canonical("eq:NVDA", -0.25) == pytest.approx(-0.25)
    with pytest.raises(ValueError):
        to_canonical("eq:NVDA", {"bogus": 1})


# --------------------------------------------------------------------------- #
# Definition + generation
# --------------------------------------------------------------------------- #
def test_drivers_only_method():
    cov = build_covariance(_panel(), window="full")
    defn = ScenarioDefinition(name="d", drivers={"eq:NVDA": -0.30},
                              method=METHOD_DRIVERS_ONLY)
    ms = ScenarioGenerator(cov).materialize(defn)
    assert ms.vector == {"eq:NVDA": -0.30}        # nothing propagated
    assert ms.drivers == ("eq:NVDA",)


def test_conditional_propagation_and_overlays():
    cov = build_covariance(_panel(), window="full")
    defn = ScenarioDefinition(
        name="nvda_shock",
        drivers={"eq:NVDA": -0.20},
        overlays={"eqvol": 0.08},          # idiosyncratic add-on, outside Σ
    )
    ms = ScenarioGenerator(cov).materialize(defn)
    assert ms.vector["eq:NVDA"] == pytest.approx(-0.20)
    assert ms.vector["eq:TSM"] == pytest.approx(-0.30, abs=1e-9)    # 1.5 * -0.20 (peer)
    assert ms.vector["credit"] == pytest.approx(0.04, abs=1e-9)     # -0.2 * -0.20
    assert ms.vector["eqvol"] == pytest.approx(0.08)               # overlay applied
    assert ms.provenance["covariance"]["cov_id"] == cov.cov_id


def test_severity_scale_multiplies_drivers():
    cov = build_covariance(_panel(), window="full")
    base = ScenarioGenerator(cov).materialize(
        ScenarioDefinition(name="b", drivers={"eq:NVDA": -0.20}))
    severe = ScenarioGenerator(cov).materialize(
        ScenarioDefinition(name="s", drivers={"eq:NVDA": -0.20}, severity_scale=2.0))
    assert severe.vector["eq:NVDA"] == pytest.approx(2.0 * base.vector["eq:NVDA"])
    assert severe.vector["eq:TSM"] == pytest.approx(2.0 * base.vector["eq:TSM"], abs=1e-9)


def test_overlay_driver_outside_covariance_applied_directly():
    cov = build_covariance(_panel(), window="full")
    defn = ScenarioDefinition(name="x", drivers={"eq:NVDA": -0.10, "fx:TWD": 0.08})
    ms = ScenarioGenerator(cov).materialize(defn)
    assert ms.vector["fx:TWD"] == pytest.approx(0.08)    # not a Σ factor -> applied as-is


# --------------------------------------------------------------------------- #
# Config + real-history integration
# --------------------------------------------------------------------------- #
def test_config_files_load():
    cov_cfg = load_covariance_config()
    scen_cfg = load_scenario_config()
    assert cov_cfg["window"] in ("full", "ewma", "crisis")
    assert "taiwan_geopolitical" in scen_cfg["scenarios"]


@pytest.mark.needs_market_data
def test_named_scenario_materializes_full_vector():
    fh = FactorHistory(get_project_root(), load_pricer_config())
    cov = covariance_from_history(fh, lookback=60, window="ewma")
    scen_cfg = load_scenario_config()
    spec = scen_cfg["scenarios"]["taiwan_geopolitical"]
    defn = ScenarioDefinition.from_config("taiwan_geopolitical", spec,
                                          severity="adverse", severity_scale=1.5)
    ms = ScenarioGenerator(cov).materialize(defn)
    # every covariance factor is present in the materialized vector
    assert set(cov.factors) <= set(ms.vector)
    # the TSM overlay (-10%) stacks on the scaled+propagated systematic move
    assert ms.vector["eq:TSM"] < -0.25
    assert ms.transforms()                      # lowers to a non-empty bump list
    assert ms.provenance["severity_scale"] == 1.5
