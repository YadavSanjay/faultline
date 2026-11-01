"""scenario-event-replay C4: ``nearest_scenario`` picks the scenario with the highest cosine.

Oracle: Requirement 4 by hand: the name with the highest ``cosine`` among non-``None`` cosines; ties to
the alphabetically first name; ``None`` when no scenario has a cosine.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.event_replay import nearest_scenario

NAMES = st.text(alphabet="abcdefghij_", min_size=1, max_size=6)
COSINES = st.none() | st.sampled_from([-1.0, -0.5, 0.0, 0.25, 0.5, 1.0]) | st.floats(
    min_value=-1.0, max_value=1.0, allow_nan=False)


@pytest.mark.criterion("C4")
def test_c4_point_highest_cosine_wins(h):
    # oracle: Requirement 4: cosines 0.2, 0.7, -0.9 -> the highest is 0.7 (ai_deflation).
    scores = {"crowded_unwind": h.score(0.2), "ai_deflation": h.score(0.7), "rate_capital": h.score(-0.9)}
    assert nearest_scenario(scores) == "ai_deflation"


@pytest.mark.criterion("C4")
def test_c4_point_none_cosines_are_skipped(h):
    # oracle: Requirement 4: None cosines are ignored, so the only real cosine (-0.5, negative) wins.
    scores = {"aaa_first": h.score(None), "taiwan": h.score(-0.5), "zzz_last": h.score(None)}
    assert nearest_scenario(scores) == "taiwan"


@pytest.mark.criterion("C4")
def test_c4_point_ties_go_to_alphabetically_first(h):
    # oracle: Requirement 4: zeta, alpha and mid tie at 0.5 (inserted zeta first) -> "alpha"; low 0.1
    #   and the None are not in the running.
    scores = {"zeta": h.score(0.5), "low": h.score(0.1), "alpha": h.score(0.5), "mid": h.score(0.5),
              "aardvark": h.score(None)}
    assert nearest_scenario(scores) == "alpha"


@pytest.mark.criterion("C4")
def test_c4_point_no_cosine_returns_none(h):
    # oracle: Requirement 4: an empty mapping and an all-None mapping have no cosine -> None; adding a
    #   single scenario with cosine -0.3 to the all-None mapping makes it the answer.
    assert nearest_scenario({}) is None
    all_none = {"taiwan": h.score(None), "tariff_shock": h.score(None)}
    assert nearest_scenario(all_none) is None
    assert nearest_scenario({**all_none, "yen_carry": h.score(-0.3)}) == "yen_carry"


@pytest.mark.criterion("C4")
@given(cosines=st.dictionaries(NAMES, COSINES, min_size=1, max_size=8))
def test_c4_property_result_cosine_is_the_max(h, cosines):
    # oracle: C4 property and Requirement 4 by hand: the result's cosine is the max over non-None
    #   cosines, and the result is the alphabetically first name carrying it; None when all are None.
    scores = {name: h.score(c) for name, c in cosines.items()}
    real = {name: c for name, c in cosines.items() if c is not None}
    result = nearest_scenario(scores)
    if not real:
        assert result is None
        return
    best = max(real.values())
    assert result in real
    assert real[result] == best
    assert result == min(name for name, c in real.items() if c == best)
