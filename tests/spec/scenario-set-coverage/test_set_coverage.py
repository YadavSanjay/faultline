"""scenario-set-coverage C3: the variance-weighted share of the book's loss directions the set spans.

Oracle: Requirement 3 by hand: ``sum(lambda_i s_i) / sum(lambda_i)``. Properties: the coverage lies between the
smallest and largest share; scaling every variance by ``c > 0`` leaves it unchanged.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.scenariogen.coverage import set_coverage

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


@pytest.mark.criterion("C3")
def test_c3_point_weighted_mean_by_hand():
    # oracle: by hand, variances (3, 2, 1) and shares (1, 0.5, 0): (3 + 1 + 0) / 6 = 2/3; variances
    # (0.5, 0.25, 0.25, 0) and shares (0.2, 0.4, 1, 0.9): (0.1 + 0.1 + 0.25 + 0) / 1 = 0.45 (a zero variance
    # is allowed and carries no weight).
    # rel 1e-12: spec tolerance
    assert set_coverage(np.array([3.0, 2.0, 1.0]), np.array([1.0, 0.5, 0.0])) == pytest.approx(2.0 / 3.0, rel=1e-12)
    got = set_coverage(np.array([0.5, 0.25, 0.25, 0.0]), np.array([0.2, 0.4, 1.0, 0.9]))
    assert got == pytest.approx(0.45, rel=1e-12)


@pytest.mark.criterion("C3")
def test_c3_refusals():
    # oracle: Requirement 3: mismatched lengths, a negative variance or a zero variance sum raises ValueError.
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        set_coverage(np.array([1.0, 2.0]), np.array([0.5, 0.5, 0.5]))
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        set_coverage(np.array([2.0, -0.5]), np.array([0.5, 0.5]))
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        set_coverage(np.array([0.0, 0.0]), np.array([0.5, 0.5]))


_VAR = st.integers(min_value=0, max_value=256).map(lambda i: i / 16)
_SHARE = st.integers(min_value=0, max_value=64).map(lambda i: i / 64)


@st.composite
def _case(draw: st.DrawFn) -> tuple[np.ndarray, np.ndarray]:
    n = draw(st.integers(min_value=1, max_value=8))
    var = np.array(draw(st.lists(_VAR, min_size=n, max_size=n)))
    assume(float(var.sum()) > 0.0)
    shares = np.array(draw(st.lists(_SHARE, min_size=n, max_size=n)))
    return var, shares


@pytest.mark.criterion("C3")
@given(case=_case(), c=st.sampled_from([0.125, 0.5, 3.0, 1e6]))
def test_c3_property_bounded_and_scale_free(case: tuple[np.ndarray, np.ndarray], c: float):
    # oracle: C3 properties: a weighted mean with non-negative weights lies within [min share, max share],
    # and scaling every weight by c > 0 cancels in the ratio; the value is the hand weighted mean
    # sum(var * share) / sum(var).
    var, shares = case
    got = set_coverage(var, shares)
    want = float(np.sum(var * shares) / np.sum(var))
    # rel 1e-12 + abs 1e-15: spec tolerance; abs covers a zero coverage
    assert got == pytest.approx(want, rel=1e-12, abs=1e-15)
    assert float(shares.min()) - 1e-12 <= got <= float(shares.max()) + 1e-12
    assert set_coverage(var * c, shares) == pytest.approx(got, rel=1e-12, abs=1e-15)
