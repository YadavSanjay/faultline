"""scenario-severity-anchor C5: the rescale factor that puts a composed scenario's lead move on its anchor.

Oracle: Requirement 5 by hand: ``r = lead_composed / lead_authored`` (1 when ``lead_authored`` is 0) and
``k = anchor * clamp(r, 1 - band, 1 + band) / lead_composed``. Values are dyadic, so the hand results are exact.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.severity import anchor_scale

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


@pytest.mark.criterion("C5")
def test_c5_point_inside_above_below_and_negative():
    # oracle: Requirement 5 by hand, band 0.25, authored -0.25, anchor -0.5:
    #   composed -0.28125: r 1.125 inside -> target -0.5625, k = -0.5625 / -0.28125 = 2.0
    #   composed -0.5:     r 2.0 -> 1.25   -> target -0.625,  k = 1.25
    #   composed -0.125:   r 0.5 -> 0.75   -> target -0.375,  k = 3.0
    #   composed 0.125:    r -0.5 -> 0.75  -> target -0.375,  k = -3.0
    #   composed -0.3125:  r 1.25 on the edge -> target -0.625, k = 2.0
    cases = [(-0.28125, 2.0), (-0.5, 1.25), (-0.125, 3.0), (0.125, -3.0), (-0.3125, 2.0)]
    for composed, k in cases:
        # rel 1e-12: spec tolerance (dyadic, exact)
        assert anchor_scale(composed, -0.25, -0.5) == pytest.approx(k, rel=1e-12), composed


@pytest.mark.criterion("C5")
def test_c5_point_zero_authored_up_direction_and_band():
    # oracle: Requirement 5 by hand:
    #   authored 0 -> r 1: composed -0.25, anchor -0.75 -> k = 3.0
    #   up (rates): composed 0.0078125, authored 0.0078125 (r 1), anchor 0.03125 -> k = 4.0
    #   band 0: r 2.0 -> 1 -> k = anchor / composed = -0.5 / -0.5 = 1.0
    #   band 0.5: composed -0.5, authored -0.25 (r 2.0 -> 1.5) -> target -0.75 -> k = 1.5;
    #             composed -0.0625 (r 0.25 -> 0.5) -> target -0.25 -> k = 4.0
    # rel 1e-12: spec tolerance (dyadic, exact)
    assert anchor_scale(-0.25, 0.0, -0.75) == pytest.approx(3.0, rel=1e-12)
    assert anchor_scale(0.0078125, 0.0078125, 0.03125) == pytest.approx(4.0, rel=1e-12)
    assert anchor_scale(-0.5, -0.25, -0.5, band=0.0) == pytest.approx(1.0, rel=1e-12)
    assert anchor_scale(-0.5, -0.25, -0.5, band=0.5) == pytest.approx(1.5, rel=1e-12)
    assert anchor_scale(-0.0625, -0.25, -0.5, band=0.5) == pytest.approx(4.0, rel=1e-12)


@pytest.mark.criterion("C5")
def test_c5_refusals():
    # oracle: Requirement 5: lead_composed = 0, band outside [0, 1) or a non-finite input raises ValueError.
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        anchor_scale(0.0, -0.25, -0.5)
    for band in (-0.125, 1.0, 1.5):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            anchor_scale(-0.25, -0.25, -0.5, band=band)
    for bad in (math.nan, math.inf, -math.inf):
        for args in ((bad, -0.25, -0.5), (-0.25, bad, -0.5), (-0.25, -0.25, bad)):
            with pytest.raises(ValueError, match=ANY_MESSAGE):
                anchor_scale(*args)
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            anchor_scale(-0.25, -0.25, -0.5, band=bad)


_NONZERO = st.integers(min_value=1, max_value=256).flatmap(lambda i: st.sampled_from([i / 256, -i / 256]))
_BAND = st.sampled_from([0.0, 0.125, 0.25, 0.5, 0.75])


@pytest.mark.criterion("C5")
@given(
    authored=_NONZERO,
    anchor=_NONZERO,
    band=_BAND,
    t=st.integers(min_value=-8, max_value=8),
)
def test_c5_property_inside_band_lands_on_anchor_times_r(authored: float, anchor: float, band: float, t: int):
    # oracle: C5 identity: for r inside the band, the rescaled lead k * lead_composed equals anchor * r.
    r = 1.0 + band * t / 8  # t/8 in [-1, 1]: r in [1 - band, 1 + band], dyadic
    composed = authored * r  # nonzero: r >= 1 - 0.75
    k = anchor_scale(composed, authored, anchor, band=band)
    # rel 1e-12: spec tolerance
    assert k * composed == pytest.approx(anchor * r, rel=1e-12)


@pytest.mark.criterion("C5")
@given(composed=_NONZERO, authored=st.one_of(st.just(0.0), _NONZERO), anchor=_NONZERO, band=_BAND)
def test_c5_property_clamped_identity(composed: float, authored: float, anchor: float, band: float):
    # oracle: Requirement 5 computed in the test: k * lead_composed = anchor * clamp(r, 1 - band, 1 + band).
    r = 1.0 if not authored else composed / authored
    clamped = min(max(r, 1.0 - band), 1.0 + band)
    k = anchor_scale(composed, authored, anchor, band=band)
    # rel 1e-12: spec tolerance
    assert k * composed == pytest.approx(anchor * clamped, rel=1e-12)
