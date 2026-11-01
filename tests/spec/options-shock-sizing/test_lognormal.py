"""options-shock-sizing C3: ``lognormal_quantile`` and ``lognormal_tail_prob``, the index-vol fallback.

Oracle: Requirement 3 closed forms with ``ln(1 + R) ~ N(-vol^2 t / 2, vol^2 t)``:
``Q(q) = exp(-vol^2 t / 2 + vol sqrt(t) PhiInv(q)) - 1``; ``P(R <= m) = Phi((ln(1+m) + vol^2 t/2) / (vol sqrt t))``
for ``m < 0`` and ``1 - Phi(...)`` for ``m > 0``. ``Phi`` from ``math.erf``, ``PhiInv`` from
``statistics.NormalDist`` (computed in the test).
"""

from __future__ import annotations

import math
from statistics import NormalDist

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.scenariogen.options.sizing import lognormal_quantile, lognormal_tail_prob

NAN = float("nan")
INF = float("inf")


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("q", "vol", "t"),
    [(0.01, 0.5, 30 / 365), (0.05, 0.35, 30 / 365), (0.5, 0.8, 0.25), (0.95, 0.2, 1.0), (0.0025, 0.6, 21 / 252)],
)
def test_c3_point_quantile_closed_form(q, vol, t):
    # oracle: Requirement 3 closed form computed here with statistics.NormalDist().inv_cdf.
    want = math.exp(-vol * vol * t / 2 + vol * math.sqrt(t) * NormalDist().inv_cdf(q)) - 1
    assert lognormal_quantile(q, vol, t) == pytest.approx(want, rel=1e-9)  # spec tolerance (rel 1e-9)


@pytest.mark.criterion("C3")
def test_c3_point_median_is_minus_half_variance():
    # oracle: hand calculation: PhiInv(0.5) = 0, so Q(0.5) = exp(-0.4^2 * 0.5 / 2) - 1 = exp(-0.04) - 1.
    assert lognormal_quantile(0.5, 0.4, 0.5) == pytest.approx(math.exp(-0.04) - 1, rel=1e-9)  # spec rel 1e-9


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("move", "vol", "t"),
    [(-0.2, 0.6, 0.25), (-0.05, 0.3, 30 / 365), (0.15, 0.6, 0.25), (0.4, 0.5, 1.0), (-0.9, 1.5, 2.0)],
)
def test_c3_point_tail_prob_closed_form(move, vol, t):
    # oracle: Requirement 3 closed form computed here with math.erf:
    #   z = (ln(1+m) + vol^2 t/2) / (vol sqrt t); P = Phi(z) for m < 0, 1 - Phi(z) for m > 0.
    z = (math.log(1 + move) + vol * vol * t / 2) / (vol * math.sqrt(t))
    cdf = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    want = cdf if move < 0 else 1 - cdf
    assert lognormal_tail_prob(move, vol, t) == pytest.approx(want, rel=1e-9)  # spec tolerance (rel 1e-9)


@pytest.mark.criterion("C3")
def test_c3_point_tail_prob_hand_value():
    # oracle: hand calculation: vol=0.4, t=1, m = exp(-0.08 - 0.4) - 1 so ln(1+m) + 0.08 = -0.4,
    #   z = -0.4 / 0.4 = -1 -> P(R <= m) = Phi(-1); upside m = exp(-0.08 + 0.4) - 1 -> P(R >= m) = 1 - Phi(1).
    phi_m1 = 0.5 * (1 + math.erf(-1 / math.sqrt(2)))
    assert lognormal_tail_prob(math.exp(-0.48) - 1, 0.4, 1.0) == pytest.approx(phi_m1, rel=1e-9)  # spec rel 1e-9
    assert lognormal_tail_prob(math.exp(0.32) - 1, 0.4, 1.0) == pytest.approx(phi_m1, rel=1e-9)  # symmetry of Phi


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("q", "vol", "t"),
    [
        (0.0, 0.3, 0.1), (1.0, 0.3, 0.1), (-0.1, 0.3, 0.1), (1.2, 0.3, 0.1),   # q outside (0, 1)
        (0.05, 0.0, 0.1), (0.05, -0.2, 0.1), (0.05, NAN, 0.1), (0.05, INF, 0.1),  # vol
        (0.05, 0.3, 0.0), (0.05, 0.3, -1.0), (0.05, 0.3, NAN), (0.05, 0.3, INF),  # t_years
    ],
)
def test_c3_point_quantile_invalid_raises(q, vol, t):
    # oracle: Requirement 3: each listed invalid input raises ValueError.
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        lognormal_quantile(q, vol, t)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("move", "vol", "t"),
    [
        (0.0, 0.3, 0.1), (-1.0, 0.3, 0.1), (-1.5, 0.3, 0.1),                  # move == 0 or <= -1
        (-0.1, 0.0, 0.1), (-0.1, -0.2, 0.1), (-0.1, NAN, 0.1), (-0.1, INF, 0.1),  # vol
        (-0.1, 0.3, 0.0), (-0.1, 0.3, -1.0), (-0.1, 0.3, NAN), (-0.1, 0.3, INF),  # t_years
    ],
)
def test_c3_point_tail_prob_invalid_raises(move, vol, t):
    # oracle: Requirement 3: each listed invalid input raises ValueError.
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        lognormal_tail_prob(move, vol, t)


_vol = st.floats(min_value=0.05, max_value=2.0)
_t = st.floats(min_value=0.01, max_value=2.0)


@pytest.mark.criterion("C3")
@given(q=st.floats(min_value=1e-4, max_value=0.999), vol=_vol, t=_t)
def test_c3_property_inverse_on_downside(h, q, vol, t):
    # oracle: Requirement 3: P(R <= Q(q)) = q for q < P(R <= 0) = Phi(vol sqrt(t) / 2) (closed form here).
    assume(q < h.phi(vol * math.sqrt(t) / 2) - 1e-9)
    x = lognormal_quantile(q, vol, t)
    assert x == pytest.approx(h.hand_lognormal_quantile(q, vol, t), rel=1e-9)  # spec rel 1e-9
    assume(x < 0)  # a q a hair below P(R <= 0) can round Q(q) to 0, where the tail prob is undefined
    assert lognormal_tail_prob(x, vol, t) == pytest.approx(q, rel=0, abs=1e-9)  # spec: within 1e-9


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(q=st.floats(min_value=1e-4, max_value=0.49), vol=st.floats(min_value=0.05, max_value=1.0), t=_t,
       k=st.floats(min_value=1.01, max_value=2.0))
def test_c3_property_downside_more_extreme_as_vol_grows(h, q, vol, t, k):
    # oracle: Requirement 3: d/dvol of -vol^2 t/2 + vol sqrt(t) PhiInv(q) = -vol t + sqrt(t) PhiInv(q) < 0
    #   for q < 0.5, so Q(q) strictly falls as vol grows; anchored to the closed form.
    lo = lognormal_quantile(q, vol, t)
    hi = lognormal_quantile(q, vol * k, t)
    assert lo == pytest.approx(h.hand_lognormal_quantile(q, vol, t), rel=1e-9)  # spec rel 1e-9
    assert hi < lo
