"""Tests for the B4 option-signal provider + the market-implied plausibility hook.

Offline: the provider is built from small in-memory frames, and the plausibility hook is exercised
with a fake provider, so nothing touches data/ or the network.
"""

from __future__ import annotations

import numpy as np
import pytest
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.options.provider import OptionSignalProvider, _NameSignals
from src.scenariogen.plausibility import PlausibilityChecker


def _provider() -> OptionSignalProvider:
    # NVDA: spot 200, a CDF rising 0..1 over strikes 100..300 (linear -> uniform density stand-in)
    strikes = np.linspace(100.0, 300.0, 201)
    cdf = np.clip((strikes - 100.0) / 200.0, 0.0, 1.0)
    nvda = _NameSignals(spot=200.0, atm_iv=0.45, skew_25d=0.05, term_ratio=1.1, vrp=-0.03,
                        strikes=strikes, cdf=cdf)
    calm = _NameSignals(spot=100.0, atm_iv=0.2, skew_25d=0.0, term_ratio=0.95, vrp=0.02,
                        strikes=None, cdf=None)
    return OptionSignalProvider({"NVDA": nvda, "CALM": calm})


def test_implied_prob_downside_reads_the_cdf():
    p = _provider()
    # -25% move -> target 150 -> CDF = (150-100)/200 = 0.25
    assert p.implied_prob("NVDA", -0.25) == pytest.approx(0.25, abs=1e-6)
    # +25% move -> target 250 -> 1 - CDF(250) = 1 - 0.75 = 0.25
    assert p.implied_prob("NVDA", 0.25) == pytest.approx(0.25, abs=1e-6)


def test_implied_prob_outside_grid_is_none_not_extrapolated():
    p = _provider()
    assert p.implied_prob("NVDA", -0.90) is None      # target 20, below grid
    assert p.implied_prob("NVDA", 1.00) is None        # target 400, above grid


def test_implied_prob_missing_name_or_curve_is_none():
    p = _provider()
    assert p.implied_prob("CALM", -0.2) is None        # no RND curve
    assert p.implied_prob("UNKNOWN", -0.2) is None
    assert p.implied_prob("NVDA", 0.0) is None          # zero move


def test_severity_multiplier_is_removed():
    # options-shock-sizing: the uncalibrated term/skew/VRP multiplier is gone (it could shrink a stress).
    assert not hasattr(_provider(), "severity_multiplier")


def _cov() -> FactorCovariance:
    f = ("eq:NVDA",)
    return FactorCovariance(factors=f, mean=np.array([0.0]), cov=np.array([[1e-4]]),
                            n_obs=100, window="test", cov_id="t")


def _scenario(move: float) -> MaterializedScenario:
    return MaterializedScenario(name="s", vector={"eq:NVDA": move}, severity="severe",
                                provenance={"horizon_days": 1})


def test_market_implied_check_is_noop_without_provider():
    chk = PlausibilityChecker(_cov(), {"bounds": {}, "envelope": {"severe_action": "ignore"}})
    res = chk.check(_scenario(-0.30))
    assert not any(f.check == "market_implied" for f in res.findings)


def test_market_implied_flags_an_extreme_move_but_never_fails():
    cfg = {"bounds": {}, "envelope": {"severe_action": "ignore"},
           "market_implied": {"enabled": True, "min_prob": 0.10}}
    chk = PlausibilityChecker(_cov(), cfg, market=_provider())
    # -45% -> target 110 -> CDF 0.05 < 0.10 -> flagged
    res = chk.check(_scenario(-0.45))
    mi = [f for f in res.findings if f.check == "market_implied"]
    assert len(mi) == 1
    assert mi[0].severity == "flag"
    assert res.promotable                       # flag-only: never gates out
    assert mi[0].observed == pytest.approx(0.05, abs=1e-6)


def test_market_implied_does_not_flag_a_move_the_market_prices():
    cfg = {"bounds": {}, "envelope": {"severe_action": "ignore"},
           "market_implied": {"enabled": True, "min_prob": 0.10}}
    chk = PlausibilityChecker(_cov(), cfg, market=_provider())
    # -20% -> target 160 -> CDF 0.30 >= 0.10 -> not flagged
    res = chk.check(_scenario(-0.20))
    assert not any(f.check == "market_implied" for f in res.findings)
