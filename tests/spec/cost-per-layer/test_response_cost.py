"""cost-per-layer C2: ``layer_cost.response_cost`` reads a service's reported ``usage.cost_usd``.

Oracle: Requirement 2 by hand: ``None`` when the response is not a mapping, has no ``usage``, ``usage``
is not a mapping, or ``cost_usd`` is missing or ``None``; ``float(cost_usd)`` for a finite int/float
``>= 0`` (not a bool); ``ValueError`` for a negative, NaN, infinite, bool or non-numeric cost.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.layer_cost import response_cost

ABSENT = [
    None,                                             # not a mapping
    "usage",                                          # not a mapping (a string)
    [("usage", {"cost_usd": 1.0})],                   # not a mapping (a list of pairs)
    42,                                               # not a mapping (a number)
    {},                                               # no "usage" key
    {"answer": "x", "cached": True},                  # no "usage" key
    {"usage": None},                                  # usage not a mapping
    {"usage": 0.5},                                   # usage not a mapping
    {"usage": [0.5]},                                 # usage not a mapping
    {"usage": {}},                                    # no "cost_usd" key
    {"usage": {"tokens": 120}},                       # no "cost_usd" key
    {"usage": {"cost_usd": None}},                    # cost_usd is None
]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("response", ABSENT)
def test_c2_point_absent_cost_is_none(response):
    # oracle: Requirement 2 by hand, first bullet: each of these reports no cost -> None.
    # red-exempt: none the correct answer for an absent cost is None (other tests pin the present case)
    # red-exempt: empty a float | None result has no empty value, so this variant returns None too
    # red-exempt: zero_instance a float | None result has no zero instance, so this variant returns None too
    assert response_cost(response) is None


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        (3, 3.0),           # int -> float
        (0.125, 0.125),     # float
        (12.5, 12.5),
    ],
)
def test_c2_point_reported_cost_as_float(cost, expected):
    # oracle: Requirement 2 by hand, second bullet: float(cost_usd); the values are exact in binary.
    out = response_cost({"answer": "x", "usage": {"cost_usd": cost, "tokens": 10}})
    assert type(out) is float
    assert out == pytest.approx(expected, abs=0.0)  # exact: float() of an exactly representable value


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("cost", [0, 0.0])
def test_c2_point_reported_zero_cost_is_zero_not_none(cost):
    # oracle: Requirement 2 by hand: 0 is a finite number >= 0, so a reported zero cost is 0.0 (a float),
    #   not None.
    # red-exempt: zero the correct answer is 0.0; the non-zero cases above pin the value
    out = response_cost({"usage": {"cost_usd": cost}})
    assert type(out) is float
    assert out == pytest.approx(0.0, abs=0.0)  # exact: float(0) is 0.0


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    "cost",
    [-0.01, -1, math.nan, math.inf, -math.inf, True, False, "0.1", "free", [0.1], {"usd": 0.1}],
)
def test_c2_point_broken_cost_raises(cost):
    # oracle: Requirement 2 by hand, third bullet: negative, NaN, infinite, bool and non-numeric costs are
    #   a service bug -> ValueError.
    with pytest.raises(ValueError, match=r"\S"):
        response_cost({"usage": {"cost_usd": cost}})


@pytest.mark.criterion("C2")
@given(cost=st.one_of(st.integers(min_value=0, max_value=10**9),
                      st.floats(min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False)))
def test_c2_property_any_finite_non_negative_number_round_trips(cost):
    # oracle: Requirement 2 by hand: for any finite number >= 0 the result is that number as a float.
    out = response_cost({"usage": {"cost_usd": cost}})
    assert type(out) is float
    assert out == pytest.approx(float(cost), rel=0.0, abs=0.0)  # exact: float(x) is the specified value


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(cost=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
       extra=st.dictionaries(st.sampled_from(["answer", "cached", "sources", "model"]),
                             st.one_of(st.text(max_size=5), st.booleans(), st.integers())))
def test_c2_metamorphic_other_keys_never_change_the_cost(cost, extra):
    # oracle: Requirement 2 by hand: only usage.cost_usd matters, so adding other top-level keys or other
    #   usage keys leaves the result unchanged (== cost); removing cost_usd makes it None.
    base = response_cost({"usage": {"cost_usd": cost}})
    noisy = response_cost({**extra, "usage": {"tokens": 7, "cost_usd": cost}})
    assert base == pytest.approx(cost, rel=0.0, abs=0.0)  # exact: float(x) for a float x is x
    assert noisy == base
    assert response_cost({**extra, "usage": {"tokens": 7}}) is None
