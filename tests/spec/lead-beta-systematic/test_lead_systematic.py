"""lead-beta-systematic C1: each target's systematic move is its one-factor beta to the lead factor.

Oracle: Requirement 1 by hand. For a target ``t``:
``mean[t] + cov[t, lead] / cov[lead, lead] * (lead_move - mean[lead])``; the lead itself, unknown targets
and duplicates are left out, keys follow first appearance. The point inputs are dyadic, so the hand values
are exact. The single-driver identity uses the existing ``FactorCovariance.conditional_mean``.
"""

from __future__ import annotations

import copy
import math

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.severity import lead_systematic

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

LEAD, A, B = "eq:LEAD", "eq:A", "eq:B"


def _cov(factors, mean, cov):
    return FactorCovariance(factors=tuple(factors), mean=np.array(mean, dtype=float),
                            cov=np.array(cov, dtype=float), n_obs=500, window="full", cov_id="synthetic")


def _synthetic():
    # Diagonally dominant (0.25 > 0.125 + 0.0625, 1 > 0.125, 0.5 > 0.0625), so a valid covariance.
    return _cov(
        [A, LEAD, B],                       # lead deliberately not first: indexing goes through cov.factors
        [0.125, 0.0625, -0.5],
        [[1.0, 0.125, 0.0],
         [0.125, 0.25, -0.0625],
         [0.0, -0.0625, 0.5]],
    )


# --------------------------------------------------------------------------- #
# Point oracles
# --------------------------------------------------------------------------- #
@pytest.mark.criterion("C1")
def test_c1_point_hand_moves_positive_and_negative_covariance():
    # oracle: hand calculation: mean[lead] 0.0625, var[lead] 0.25.
    #   lead_move -0.9375 -> delta = -1.
    #   A: beta = 0.125 / 0.25 = 0.5   -> 0.125 + 0.5 * (-1)     = -0.375
    #   B: beta = -0.0625 / 0.25 = -0.25 -> -0.5 + (-0.25) * (-1) = -0.25
    #   lead_move 1.0625 -> delta = +1: A 0.125 + 0.5 = 0.625; B -0.5 - 0.25 = -0.75.
    cov = _synthetic()
    got = lead_systematic(cov, LEAD, -0.9375, [A, B])
    assert list(got) == [A, B]
    # abs 1e-15: dyadic inputs, every step exact
    assert got[A] == pytest.approx(-0.375, abs=1e-15)
    assert got[B] == pytest.approx(-0.25, abs=1e-15)
    up = lead_systematic(cov, LEAD, 1.0625, [B, A])
    assert list(up) == [B, A]
    # abs 1e-15: dyadic inputs, every step exact
    assert up[A] == pytest.approx(0.625, abs=1e-15)
    assert up[B] == pytest.approx(-0.75, abs=1e-15)


@pytest.mark.criterion("C1")
def test_c1_point_lead_unknown_and_duplicates_left_out():
    # oracle: Requirement 1: the lead itself and unknown targets are left out, duplicates appear once, keys in
    #   first-appearance order; values as the hand calculation above (delta -1: A -0.375, B -0.25);
    #   no targets (or only the lead / unknowns) -> {}.
    cov = _synthetic()
    got = lead_systematic(cov, LEAD, -0.9375, [B, "eq:NOPE", LEAD, A, B, A, "rates:UST_10Y"])
    assert list(got) == [B, A]
    # abs 1e-15: dyadic inputs, every step exact
    assert got[B] == pytest.approx(-0.25, abs=1e-15)
    assert got[A] == pytest.approx(-0.375, abs=1e-15)
    assert lead_systematic(cov, LEAD, -0.9375, []) == {}
    assert lead_systematic(cov, LEAD, -0.9375, [LEAD, "eq:NOPE", LEAD]) == {}


@pytest.mark.criterion("C1")
def test_c1_point_at_lead_mean_every_target_at_its_mean():
    # oracle: Requirement 1: lead_move = mean[lead] = 0.0625 -> delta 0 -> each target at its mean
    #   (A 0.125, B -0.5).
    got = lead_systematic(_synthetic(), LEAD, 0.0625, [A, B])
    assert list(got) == [A, B]
    # abs 1e-15: delta is exactly 0
    assert got[A] == pytest.approx(0.125, abs=1e-15)
    assert got[B] == pytest.approx(-0.5, abs=1e-15)


@pytest.mark.criterion("C1")
def test_c1_point_iterable_targets_and_inputs_not_mutated():
    # oracle: Requirement 1: targets may be any iterable (here a one-shot generator); the inputs are not
    #   mutated; values as the hand calculation (delta -1: A -0.375, B -0.25).
    cov = _synthetic()
    targets = [A, LEAD, B]
    before = (cov.factors, cov.mean.copy(), cov.cov.copy(), copy.copy(targets))
    got = lead_systematic(cov, LEAD, -0.9375, (t for t in targets))
    assert list(got) == [A, B]
    # abs 1e-15: dyadic inputs, every step exact
    assert got[A] == pytest.approx(-0.375, abs=1e-15)
    assert got[B] == pytest.approx(-0.25, abs=1e-15)
    assert cov.factors == before[0]
    assert np.array_equal(cov.mean, before[1])
    assert np.array_equal(cov.cov, before[2])
    assert targets == before[3]


@pytest.mark.criterion("C1")
def test_c1_point_refusals():
    # oracle: Requirement 1: ValueError for an unknown lead, cov[lead, lead] <= 0, and a NaN or infinite
    #   lead move.
    cov = _synthetic()
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        lead_systematic(cov, "eq:NOPE", -0.9375, [A, B])
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            lead_systematic(cov, LEAD, bad, [A, B])
    for var in (0.0, -0.25):
        flat = _cov([A, LEAD, B], [0.125, 0.0625, -0.5],
                    [[1.0, 0.0, 0.0], [0.0, var, 0.0], [0.0, 0.0, 0.5]])
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            lead_systematic(flat, LEAD, -0.9375, [A, B])


# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #
NAMES = ("f0", "f1", "f2")
dyadic = st.integers(min_value=-16, max_value=16).map(lambda i: i / 8.0)
mean_st = st.integers(min_value=-16, max_value=16).map(lambda i: i / 64.0)
delta_st = st.integers(min_value=-64, max_value=64).filter(lambda i: i != 0).map(lambda i: i / 32.0)


@st.composite
def covariances(draw):
    """(FactorCovariance, lead): 3 factors, cov = L L^T + I/16 (positive definite), dyadic L and means."""
    lmat = np.array([[draw(dyadic) for _ in NAMES] for _ in NAMES])
    sigma = lmat @ lmat.T + np.eye(len(NAMES)) / 16.0
    mean = [draw(mean_st) for _ in NAMES]
    lead = draw(st.sampled_from(NAMES))
    return _cov(NAMES, mean, sigma), lead


def _hand(cov, lead, lead_move, t):
    i, j = cov.factors.index(t), cov.factors.index(lead)
    return float(cov.mean[i] + cov.cov[i, j] / cov.cov[j, j] * (lead_move - cov.mean[j]))


def _others(lead):
    return [t for t in NAMES if t != lead]


@pytest.mark.criterion("C1")
@given(cl=covariances(), d=delta_st)
def test_c1_property_matches_hand_formula_and_single_driver_conditional_mean(cl, d):
    # oracle: Requirement 1 by hand (formula computed in the test) and C1's identity with the existing
    #   cov.conditional_mean({lead: lead_move})[target].
    cov, lead = cl
    move = float(cov.mean[cov.factors.index(lead)]) + d
    got = lead_systematic(cov, lead, move, NAMES)
    cm = cov.conditional_mean({lead: move})
    assert list(got) == _others(lead)
    for t in _others(lead):
        # rel 1e-12 (+ abs 1e-14 for values near 0): spec tolerance; pinv vs a scalar division differ by rounding
        assert got[t] == pytest.approx(_hand(cov, lead, move, t), rel=1e-12, abs=1e-14)
        assert got[t] == pytest.approx(cm[t], rel=1e-12, abs=1e-14)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(cl=covariances(), d=delta_st)
def test_c1_property_affine_in_lead_move(cl, d):
    # oracle: metamorphic: doubling lead_move - mean[lead] doubles every result[t] - mean[t]; lead_move =
    #   mean[lead] gives every target its mean; each value equals the hand formula.
    cov, lead = cl
    mu_lead = float(cov.mean[cov.factors.index(lead)])
    one = lead_systematic(cov, lead, mu_lead + d, NAMES)
    two = lead_systematic(cov, lead, mu_lead + 2.0 * d, NAMES)
    zero = lead_systematic(cov, lead, mu_lead, NAMES)
    assert list(one) == list(two) == list(zero) == _others(lead)
    for t in _others(lead):
        mu_t = float(cov.mean[cov.factors.index(t)])
        # rel 1e-12 (+ abs 1e-14): a few roundings in the subtraction of the mean
        assert one[t] == pytest.approx(_hand(cov, lead, mu_lead + d, t), rel=1e-12, abs=1e-14)
        assert two[t] - mu_t == pytest.approx(2.0 * (one[t] - mu_t), rel=1e-12, abs=1e-14)
        assert zero[t] == pytest.approx(mu_t, rel=1e-12, abs=1e-14)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(cl=covariances(), d=delta_st, a=st.sampled_from([1.0 / 64.0, 0.375, 3.0, 10.0, 1024.0]))
def test_c1_property_invariant_to_covariance_scale(cl, d, a):
    # oracle: metamorphic: scaling the whole covariance by a > 0 cancels in cov[t, lead] / cov[lead, lead],
    #   so the result is unchanged; each value equals the hand formula.
    cov, lead = cl
    move = float(cov.mean[cov.factors.index(lead)]) + d
    base = lead_systematic(cov, lead, move, NAMES)
    scaled = lead_systematic(_cov(cov.factors, cov.mean, a * cov.cov), lead, move, NAMES)
    assert list(base) == list(scaled) == _others(lead)
    for t in _others(lead):
        # rel 1e-12 (+ abs 1e-14): the scale cancels up to rounding
        assert base[t] == pytest.approx(_hand(cov, lead, move, t), rel=1e-12, abs=1e-14)
        assert scaled[t] == pytest.approx(base[t], rel=1e-12, abs=1e-14)
