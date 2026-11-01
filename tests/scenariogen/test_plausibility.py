"""S3 plausibility tests: hard bounds gate; envelope graduated flag/fail."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.risk.factor_history import FactorHistory
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.config import load_plausibility_config
from src.scenariogen.covariance import FactorCovariance, build_covariance
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.plausibility import PlausibilityChecker

CONFIG = {
    "bounds": {"equity": {"max_abs_pct": 85}, "ir": {"max_abs_bp": 400}},
    "envelope": {"n_sigma": 6.0, "severe_action": "flag", "non_severe_action": "fail"},
}


def _cov() -> FactorCovariance:
    n = 200
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(0)
    panel = pd.DataFrame({"eq:NVDA": rng.normal(0.0, 0.02, n),    # ~2% daily std
                          "ir:DGS10": rng.normal(0.0, 0.0005, n)}, index=idx)
    return build_covariance(panel, window="full")


def _scenario(vector: dict[str, float], severity: str, horizon: int = 1) -> MaterializedScenario:
    return MaterializedScenario(name="t", vector=vector, severity=severity,
                                provenance={"horizon_days": horizon})


def test_bound_violation_fails_and_gates():
    chk = PlausibilityChecker(_cov(), CONFIG)
    res = chk.check(_scenario({"eq:NVDA": -0.95}, "severe"))   # -95% > 85% hard cap
    assert res.status == "fail" and not res.promotable
    assert any(f.check == "bound" for f in res.findings)


def test_envelope_breach_flags_for_severe_fails_for_adverse():
    chk = PlausibilityChecker(_cov(), CONFIG)
    # |-0.20| >> 6 * 0.02 ~ 0.12 band -> envelope breach (well within the 85% bound)
    severe = chk.check(_scenario({"eq:NVDA": -0.20}, "severe"))
    assert severe.status == "flag" and severe.promotable        # beyond-historical, allowed
    adverse = chk.check(_scenario({"eq:NVDA": -0.20}, "adverse"))
    assert adverse.status == "fail" and not adverse.promotable  # non_severe_action=fail


def test_within_envelope_passes():
    chk = PlausibilityChecker(_cov(), CONFIG)
    res = chk.check(_scenario({"eq:NVDA": -0.05, "ir:DGS10": 0.001}, "adverse"))
    assert res.status == "pass" and res.promotable
    assert res.findings == []


def test_horizon_widens_the_envelope():
    chk = PlausibilityChecker(_cov(), CONFIG)
    vec = {"eq:NVDA": -0.20}
    assert chk.check(_scenario(vec, "adverse", horizon=1)).status == "fail"
    # a 100-day horizon widens the band by sqrt(100)=10x (~1.2) -> -20% now inside
    assert chk.check(_scenario(vec, "adverse", horizon=100)).status == "pass"


def test_default_config_flags_not_fails():
    """Default policy (flag/flag) keeps stress scenarios promotable on envelope breach."""
    cfg = {"bounds": {"equity": {"max_abs_pct": 85}},
           "envelope": {"n_sigma": 6.0, "severe_action": "flag", "non_severe_action": "flag"}}
    res = PlausibilityChecker(_cov(), cfg).check(_scenario({"eq:NVDA": -0.50}, "adverse"))
    assert res.status == "flag" and res.promotable


@pytest.mark.needs_market_data
def test_authored_scenarios_pass_the_gate():
    """Every authored named scenario at every tier is promotable under the default config.

    Replays are left out: their drivers are observed episode moves, not authored magnitudes, so the gate
    holding a scaled replay tier out is a finding about the episode, not an authoring error.
    """
    cat = ScenarioCatalog.from_config(FactorHistory(get_project_root(), load_pricer_config()),
                                      lookback=60)
    chk = PlausibilityChecker(cat.covariance, load_plausibility_config())
    authored = [n for n in cat.names() if n not in cat.replays]
    assert authored
    for name in authored:
        for tier in cat.tiers():
            ms = cat.materialize(name, tier)
            res = chk.check(ms)
            assert res.promotable, f"{ms.name}/{ms.severity} gated out: {res.to_dict()}"
