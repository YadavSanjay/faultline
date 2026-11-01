"""Unit tests for issuer-scenario-support wiring: the per-scenario severe-tier covariance.

The locked spec tests cover ``severe_covariance_source`` and the authoring keys; these cover
``ScenarioCatalog.covariance_for`` choosing the production Σ for a ``severe_sigma: "production"``
scenario (toy covariances, no data/).
"""

from __future__ import annotations

import numpy as np
import pytest
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.errors import ScenarioGenerationError


def _cov(scale: float, cov_id: str) -> FactorCovariance:
    return FactorCovariance(factors=("credit", "credit:ORCL"), mean=np.zeros(2),
                            cov=np.eye(2) * scale, n_obs=250, window="test", cov_id=cov_id)


def _catalog(*, crisis: FactorCovariance | None, crisis_error: str | None = None) -> ScenarioCatalog:
    issuer = {"severity": {"lead": "credit:ORCL", "direction": "up", "severe_sigma": "production"}}
    market = {"severity": {"lead": "eq:XLK", "direction": "down"}}
    return ScenarioCatalog(covariance=_cov(1e-6, "prod"), specs={"issuer": issuer, "market": market},
                           severity_tiers={"mild": 1.0, "adverse": 1.5, "severe": 2.0},
                           crisis=crisis, crisis_error=crisis_error)


def test_production_severe_sigma_keeps_the_production_covariance():
    cat = _catalog(crisis=_cov(4e-6, "crisis"))
    assert cat.covariance_for("severe", "issuer").cov_id == "prod"
    assert cat.covariance_for("severe", "market").cov_id == "crisis"
    assert cat.covariance_for("adverse", "issuer").cov_id == "prod"
    assert cat.generator_for("severe", "issuer").cov.cov_id == "prod"


def test_production_severe_sigma_never_hits_the_crisis_unavailable_error():
    cat = _catalog(crisis=None, crisis_error="2022 window too short")
    assert cat.covariance_for("severe", "issuer").cov_id == "prod"
    with pytest.raises(ScenarioGenerationError):
        cat.covariance_for("severe", "market")
