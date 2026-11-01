"""replay-cap-intercept wiring: anchor_tier caps a replay on the affine tier vector, so the capped tier passes
the plausibility bound check when the conditional mean has an intercept.

Synthetic covariance and anchor history; no ``data/``, services or pricing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.scenariogen.anchoring import AnchorBook, SeverityConfig, anchor_tier
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.plausibility import PlausibilityChecker
from src.scenariogen.scenario_gen import ScenarioDefinition, ScenarioGenerator

CFG = SeverityConfig()
FACTORS = ("eq:XLK", "eq:CEG", "credit")
BOUNDS = {"eq:XLK": 0.96, "eq:CEG": 0.96, "credit": 0.04}


def _gen(mean: tuple[float, float, float]) -> ScenarioGenerator:
    vols = np.array([0.02, 0.03, 0.0005])
    corr = np.full((3, 3), 0.5) + 0.5 * np.eye(3)
    return ScenarioGenerator(FactorCovariance(factors=FACTORS, mean=np.array(mean), cov=np.outer(vols, vols) * corr,
                                              n_obs=250, window="test", cov_id="t"))


def _book() -> AnchorBook:
    idx = pd.bdate_range("2000-01-03", periods=2520).strftime("%Y-%m-%d")
    moves = pd.Series(np.tile([0.004, -0.004], 1260), index=idx)
    moves.iloc[1000] = -0.30
    info = {"eq:XLK": {"source": "XLK", "proxy": None, "beta": None, "spliced": False,
                       "start": idx[0], "own_start": idx[0], "missing": False}}
    return AnchorBook({"eq:XLK": moves}, info, {"equity": {"max_abs_pct": 96}}, CFG)


def _defn() -> ScenarioDefinition:
    # a replay-like tier: the lead and credit as drivers, CEG's company leg as an overlay that runs against them
    return ScenarioDefinition(name="rp", drivers={"eq:XLK": -0.30, "credit": 0.004}, overlays={"eq:CEG": 1.5},
                              severity="severe", severity_scale=1.0, horizon_days=10)


def _capped(gen: ScenarioGenerator):
    return anchor_tier("rp", "severe", lead="eq:XLK", direction="down", composed=_defn(), authored=_defn(),
                       generator=gen, book=_book(), cfg=CFG, state_raw=1.0, state_used=1.0, bounds=BOUNDS)


def test_capped_tier_with_an_intercept_stays_inside_every_bound():
    gen = _gen((0.0, 0.002, 0.0))                       # CEG's conditional mean has an intercept
    ms = _capped(gen)
    sev = ms.provenance["severity"]
    assert sev["k_cap"] is not None and sev["k"] == sev["k_cap"] and sev["cap_binding"] == "eq:CEG"
    assert all(abs(ms.vector[f]) < b for f, b in BOUNDS.items())
    assert abs(ms.vector["eq:CEG"]) > 0.96 * (1 - 1e-6)                # it does bind: on the bound, not short
    plaus = PlausibilityChecker(gen.cov, {"bounds": {"equity": {"max_abs_pct": 96},
                                                     "credit": {"max_abs_bp": 400}}}).check(ms)
    assert not [f for f in plaus.findings if f.check == "bound"]


def test_the_proportional_cap_would_have_breached_the_bound():
    # the cause of the ai_deflation hold-out: scaling the scale-1 vector assumes no intercept
    gen = _gen((0.0, 0.002, 0.0))
    defn = _defn()
    unit = gen.materialize(defn).vector["eq:CEG"]
    k_prop = 0.96 / abs(unit)
    scaled = ScenarioDefinition(name="rp", drivers=defn.drivers, overlays={"eq:CEG": 1.5 * k_prop},
                                severity="severe", severity_scale=k_prop, horizon_days=10)
    assert abs(gen.materialize(scaled).vector["eq:CEG"]) > 0.96


def test_without_an_intercept_the_cap_is_the_proportional_one_up_to_the_margin():
    gen = _gen((0.0, 0.0, 0.0))
    ms = _capped(gen)
    unit = gen.materialize(_defn()).vector
    k_prop = min(BOUNDS[f] / abs(unit[f]) for f in BOUNDS if abs(unit[f]) > 0.0)
    assert ms.provenance["severity"]["k"] == pytest.approx(k_prop * (1 - 1e-9), rel=1e-12)
