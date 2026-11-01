"""options-shock-sizing C2: ``rnd_quantile``, the risk-neutral quantile as a return.

Oracle: Requirement 2 by hand: ``K_q / spot - 1`` with ``K_q`` linearly interpolated between the two
grid points bracketing ``q``; the first strike of a flat CDF segment equal to ``q``; None outside
``[cdf[0], cdf[-1]]``. Grid from the spec Notes: strikes 80..120, CDF 0.0, 0.02, 0.4, 0.9, 1.0, spot 100.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.scenariogen.options.sizing import rnd_quantile


@pytest.mark.criterion("C2")
def test_c2_point_interpolated_between_grid_points(h):
    # oracle: hand calculation on the Notes grid:
    #   q=0.01 in [0.0, 0.02] -> K = 80 + 10 * 0.01/0.02 = 85      -> 85/100 - 1 = -0.15
    #   q=0.05 in [0.02, 0.4] -> K = 90 + 10 * 0.03/0.38           -> K/100 - 1
    #   q=0.65 in [0.4, 0.9]  -> K = 100 + 10 * 0.25/0.5 = 105     -> 0.05
    assert rnd_quantile(h.STRIKES, h.CDF, h.SPOT, 0.01) == pytest.approx(-0.15, rel=1e-12)  # spec rel 1e-12
    want = (90 + 10 * 0.03 / 0.38) / 100 - 1
    assert rnd_quantile(h.STRIKES, h.CDF, h.SPOT, 0.05) == pytest.approx(want, rel=1e-12)  # spec rel 1e-12
    assert rnd_quantile(h.STRIKES, h.CDF, h.SPOT, 0.65) == pytest.approx(0.05, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C2")
def test_c2_point_q_on_grid_point(h):
    # oracle: hand calculation: q equal to a grid CDF value returns that strike exactly:
    #   q=0.02 -> K=90 -> -0.1; q=0.9 -> K=110 -> 0.1.
    assert rnd_quantile(h.STRIKES, h.CDF, h.SPOT, 0.02) == pytest.approx(-0.1, rel=1e-12)  # spec rel 1e-12
    assert rnd_quantile(h.STRIKES, h.CDF, h.SPOT, 0.9) == pytest.approx(0.1, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C2")
def test_c2_point_q_equal_to_first_and_last_cdf_value_is_in_grid():
    # oracle: hand calculation: q == cdf[0] is not < cdf[0], so K = strikes[0] = 90 -> 90/120 - 1 = -0.25;
    #   q == cdf[-1] is not > cdf[-1]; first strike with CDF 0.95 is 150 -> 150/120 - 1 = 0.25.
    strikes = [90.0, 110.0, 130.0, 150.0]
    cdf = [0.05, 0.3, 0.7, 0.95]
    assert rnd_quantile(strikes, cdf, 120.0, 0.05) == pytest.approx(-0.25, rel=1e-12)  # spec rel 1e-12
    assert rnd_quantile(strikes, cdf, 120.0, 0.95) == pytest.approx(0.25, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C2")
def test_c2_point_flat_segment_uses_first_strike():
    # oracle: hand calculation: CDF is 0.1 at strikes 90, 100 and 110 -> q=0.1 uses the first, 90 -> -0.1.
    #   q=0.55 in [0.1 @110, 1.0 @120] -> K = 110 + 10 * 0.45/0.9 = 115 -> 0.15 (the flat run is skipped).
    #   q=0.05 in [0.0 @80, 0.1 @90] -> K = 85 -> -0.15.
    strikes = [80.0, 90.0, 100.0, 110.0, 120.0]
    cdf = [0.0, 0.1, 0.1, 0.1, 1.0]
    assert rnd_quantile(strikes, cdf, 100.0, 0.1) == pytest.approx(-0.1, rel=1e-12)  # spec rel 1e-12
    assert rnd_quantile(strikes, cdf, 100.0, 0.55) == pytest.approx(0.15, rel=1e-12)  # spec rel 1e-12
    assert rnd_quantile(strikes, cdf, 100.0, 0.05) == pytest.approx(-0.15, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C2")
def test_c2_point_none_outside_grid():
    # oracle: hand calculation: grid CDF spans [0.01, 0.99]; q=0.005 < cdf[0] and q=0.995 > cdf[-1] -> None;
    #   control q=0.5 in [0.01 @80, 0.99 @120] -> K = 80 + 40 * 0.49/0.98 = 100 -> 100/80 - 1 = 0.25.
    strikes = [80.0, 120.0]
    cdf = [0.01, 0.99]
    assert rnd_quantile(strikes, cdf, 80.0, 0.005) is None
    assert rnd_quantile(strikes, cdf, 80.0, 0.995) is None
    assert rnd_quantile(strikes, cdf, 80.0, 0.5) == pytest.approx(0.25, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    ("strikes", "cdf", "spot", "q"),
    [
        ([80.0, 90.0, 100.0], [0.0, 0.5, 1.0], 100.0, 0.0),        # q not in (0, 1)
        ([80.0, 90.0, 100.0], [0.0, 0.5, 1.0], 100.0, 1.0),
        ([80.0, 90.0, 100.0], [0.0, 0.5, 1.0], 100.0, -0.2),
        ([80.0, 90.0, 100.0], [0.0, 0.5, 1.0], 100.0, 1.3),
        ([80.0, 90.0, 100.0], [0.0, 0.5, 1.0], 0.0, 0.5),          # non-positive spot
        ([80.0, 90.0, 100.0], [0.0, 0.5, 1.0], -100.0, 0.5),
        ([80.0, 90.0, 100.0], [0.0, 1.0], 100.0, 0.5),             # mismatched lengths
        ([80.0, 90.0], [0.0, 0.5, 1.0], 100.0, 0.5),
        ([100.0], [0.5], 100.0, 0.5),                              # fewer than 2 points
        ([], [], 100.0, 0.5),
        ([80.0, 90.0, 90.0, 100.0], [0.0, 0.3, 0.6, 1.0], 100.0, 0.5),  # strikes not strictly increasing
        ([80.0, 100.0, 90.0], [0.0, 0.5, 1.0], 100.0, 0.5),
        ([80.0, 90.0, 100.0, 110.0], [0.0, 0.6, 0.4, 1.0], 100.0, 0.5),  # CDF decreases
        ([80.0, 90.0, 100.0], [0.0, 0.5, 0.49], 100.0, 0.3),
    ],
)
def test_c2_point_invalid_inputs_raise(strikes, cdf, spot, q):
    # oracle: Requirement 2: each listed invalid input raises ValueError.
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        rnd_quantile(strikes, cdf, spot, q)


@st.composite
def _grid(draw):
    n = draw(st.integers(min_value=2, max_value=8))
    strikes = sorted(draw(st.sets(st.integers(min_value=10, max_value=400), min_size=n, max_size=n)))
    # CDF on a 1/1000 lattice: clean gaps, ties (flat segments) allowed.
    cdf = [c / 1000 for c in sorted(draw(st.lists(st.integers(min_value=0, max_value=1000), min_size=n, max_size=n)))]
    assume(cdf[-1] > cdf[0])
    spot = draw(st.floats(min_value=20.0, max_value=300.0))
    return [float(k) for k in strikes], cdf, spot


def _q_in(cdf, u):
    """A q inside [cdf[0], cdf[-1]] (clamped against rounding)."""
    return min(max(cdf[0] + (cdf[-1] - cdf[0]) * u, cdf[0]), cdf[-1])


@pytest.mark.criterion("C2")
@given(grid=_grid(), u1=st.floats(min_value=0.0, max_value=1.0), u2=st.floats(min_value=0.0, max_value=1.0))
def test_c2_property_non_decreasing_in_q(h, grid, u1, u2):
    # oracle: Requirement 2 closed form (h.hand_quantile) and monotonicity of an interpolated
    #   non-decreasing CDF's inverse.
    strikes, cdf, spot = grid
    q1, q2 = sorted((_q_in(cdf, u1), _q_in(cdf, u2)))
    assume(q1 > 0.0 and q2 < 1.0)
    r1 = rnd_quantile(strikes, cdf, spot, q1)
    r2 = rnd_quantile(strikes, cdf, spot, q2)
    assert r1 is not None and r2 is not None
    assert r1 == pytest.approx(h.hand_quantile(strikes, cdf, spot, q1), rel=1e-9, abs=1e-12)  # float interp
    assert r1 <= r2 + 1e-12  # tolerance: floating-point interpolation noise only


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(grid=_grid(), u=st.floats(min_value=0.0, max_value=1.0), c=st.floats(min_value=0.01, max_value=100.0))
def test_c2_property_scale_invariant(h, grid, u, c):
    # oracle: Requirement 2: K_q scales with the strikes, so (c K_q) / (c spot) - 1 is unchanged;
    #   anchored to the closed form h.hand_quantile.
    strikes, cdf, spot = grid
    q = _q_in(cdf, u)
    assume(0.0 < q < 1.0)
    base = rnd_quantile(strikes, cdf, spot, q)
    assert base is not None
    assert base == pytest.approx(h.hand_quantile(strikes, cdf, spot, q), rel=1e-9, abs=1e-12)  # float interp
    scaled = rnd_quantile([c * k for k in strikes], cdf, c * spot, q)
    assert scaled == pytest.approx(base, rel=1e-9, abs=1e-12)  # tolerance: rounding of c*K and c*spot
