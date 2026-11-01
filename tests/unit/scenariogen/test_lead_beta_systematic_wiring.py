"""lead-beta-systematic wiring: the anchored leg hook splits company legs on the one-factor beta to the lead, and
the correction overlay makes the leg's final move ``systematic + multiplier x company-specific``.

Toy covariance (zero means, so the mean terms vanish); no data/, services or QuantLib.
"""

from __future__ import annotations

import numpy as np
import pytest
from src.scenariogen.ablation import compose_definition
from src.scenariogen.anchoring import SeverityConfig, leg_transform
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.scenario_gen import ScenarioDefinition, ScenarioGenerator

LAYERS_ON = frozenset({"xbrlrag_narrative", "news"})
CAP = SeverityConfig().leg_cap


def _gen() -> ScenarioGenerator:
    factors = ("eq:XLK", "eq:NVDA", "eq:AMD", "credit")
    vols = np.array([0.02, 0.03, 0.03, 0.0005])
    corr = np.full((4, 4), 0.5) + 0.5 * np.eye(4)
    return ScenarioGenerator(FactorCovariance(factors=factors, mean=np.zeros(4), cov=np.outer(vols, vols) * corr,
                                              n_obs=250, window="test", cov_id="toy"))


def _inputs(news: float) -> dict:
    drivers = {"eq:XLK": -0.30, "credit": 0.004, "eq:NVDA": -0.35}
    return {"authored_drivers": drivers, "authored_overlays": {}, "macro_drivers": None,
            "seed_legs": {"eq:NVDA": -0.35}, "calibrated_legs": {"eq:NVDA": -0.35},
            "news_mult": {"eq:NVDA": news}, "crowding_mult": {}}


def _final(gen: ScenarioGenerator, out: dict) -> float:
    drivers, overlays, scale = compose_definition(layers=LAYERS_ON, tier_scale=1.0, state_mult=1.0, **out)
    defn = ScenarioDefinition(name="s", drivers=drivers, overlays=overlays, severity="mild", severity_scale=scale,
                              horizon_days=10)
    return gen.materialize(defn).vector["eq:NVDA"]


@pytest.mark.parametrize("news", [1.0, 1.2])
def test_leg_final_move_is_lead_beta_systematic_plus_scaled_company_part(news):
    gen = _gen()
    record: dict = {}
    out = leg_transform(gen, CAP, record, lead="eq:XLK")(_inputs(news), LAYERS_ON)
    beta = 0.5 * 0.03 * 0.02 / 0.02 ** 2                    # cov(NVDA, XLK) / var(XLK) = 0.75
    systematic = beta * -0.30
    assert out["seed_legs"]["eq:NVDA"] == pytest.approx(-0.35 - systematic, rel=1e-12)
    assert record["systematic"] == {"rule": "lead_beta", "lead": "eq:XLK", "lead_move": -0.30}
    m = out["news_mult"]["eq:NVDA"]                          # after the leg cap
    assert _final(gen, out) == pytest.approx(systematic + m * (-0.35 - systematic), rel=1e-9)
    if news == 1.0:
        assert _final(gen, out) == pytest.approx(-0.35, rel=1e-9)   # no double count at unit multipliers


def test_a_leg_equal_to_the_lead_keeps_its_whole_move_company_specific():
    gen = _gen()
    out = leg_transform(gen, CAP, {}, lead="eq:NVDA")(_inputs(1.2), LAYERS_ON)
    assert out["seed_legs"]["eq:NVDA"] == pytest.approx(-0.35, rel=1e-12)
    assert _final(gen, out) == pytest.approx(out["news_mult"]["eq:NVDA"] * -0.35, rel=1e-9)


def test_compose_without_corrections_is_unchanged_and_corrections_scale_with_the_tier():
    kw = {"layers": LAYERS_ON, "authored_drivers": {"eq:XLK": -0.30}, "authored_overlays": {},
          "macro_drivers": None, "seed_legs": {"eq:NVDA": -0.1}, "calibrated_legs": {"eq:NVDA": -0.1},
          "news_mult": {"eq:NVDA": 1.5}, "crowding_mult": {}, "tier_scale": 2.0, "state_mult": 1.0}
    assert compose_definition(**kw) == compose_definition(**kw, leg_corrections=None)
    _, base, _ = compose_definition(**kw)
    _, corrected, _ = compose_definition(**kw, leg_corrections={"eq:NVDA": 0.05})
    assert corrected["eq:NVDA"] - base["eq:NVDA"] == pytest.approx(0.05 * 2.0, rel=1e-12)   # tier, not news


def test_without_a_lead_the_old_rule_applies_and_no_correction_is_made():
    gen = _gen()
    out = leg_transform(gen, CAP, {})(_inputs(1.0), LAYERS_ON)
    assert "leg_corrections" not in out
    assert _final(gen, out) == pytest.approx(-0.35, rel=1e-9)
