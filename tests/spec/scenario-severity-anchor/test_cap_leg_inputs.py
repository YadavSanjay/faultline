"""scenario-severity-anchor C3: bounding each company leg's compound multiplier ``m = a * n * c``.

Oracle: Requirement 3 by hand: ``a = calibrated / seed`` (1 when the seed is 0), ``n`` and ``c`` default to 1;
inside ``[1/cap, cap]`` a leg is unchanged, outside each factor becomes ``x**lam`` with
``lam = ln(cap) / ln(m)`` (``m > cap``) or ``ln(1/cap) / ln(m)`` (``m < 1/cap``), and the capped calibrated
move is ``seed * a**lam``. With cap 2 and ``m`` = 4 or 1/4, ``lam`` = 1/2, so the hand values are square roots.
"""

from __future__ import annotations

import copy
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.severity import cap_leg_inputs

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

SEED = {
    "eq:A": -0.25,  # a = 4,    n = 2,   c = 0.5 -> m = 4 > 2: lam 1/2
    "eq:B": 1.0,  # a = 0.25, n = 0.5, c = 2   -> m = 1/4 < 1/2: lam 1/2
    "eq:C": 0.5,  # a = 1.5,  n = 1.25, no c   -> m = 1.875, inside
    "eq:D": 0.5,  # a = 4,    no n, no c       -> m = 4: lam 1/2
    "eq:E": 0.0,  # seed 0 -> a = 1, n = 4, no c -> m = 4: lam 1/2
    "eq:F": -0.5,  # a = 2,   no n, no c       -> m = 2 = cap, unchanged
    "eq:G": 0.25,  # a = 0.5, no n, c = 1      -> m = 1/2 = 1/cap, unchanged
    "eq:H": 0.125,  # a = 1,  n = 1.5, no c     -> m = 1.5, inside
}
CAL = {"eq:A": -1.0, "eq:B": 0.25, "eq:C": 0.75, "eq:D": 2.0, "eq:E": 0.0, "eq:F": -1.0, "eq:G": 0.125, "eq:H": 0.125}
NEWS = {"eq:A": 2.0, "eq:B": 0.5, "eq:C": 1.25, "eq:E": 4.0, "eq:H": 1.5}
CROWD = {"eq:A": 0.5, "eq:B": 2.0, "eq:G": 1.0}

R2 = math.sqrt(2.0)
RH = math.sqrt(0.5)


@pytest.mark.criterion("C3")
def test_c3_point_cap_two():
    # oracle: Requirement 3 by hand, cap 2 (lam = 1/2 for every capped leg):
    #   A: a 4 -> 2, cal -0.25*2 = -0.5; n 2 -> sqrt 2; c 0.5 -> sqrt 0.5
    #   B: a 0.25 -> 0.5, cal 1*0.5 = 0.5; n 0.5 -> sqrt 0.5; c 2 -> sqrt 2
    #   C, F, G, H unchanged (inside or on the bound); D: a 4 -> 2, cal 0.5*2 = 1.0; E: cal 0*1 = 0, n 4 -> 2
    seed, cal, news, crowd = (copy.deepcopy(x) for x in (SEED, CAL, NEWS, CROWD))
    out_cal, out_news, out_crowd = cap_leg_inputs(seed, cal, news, crowd, cap=2.0)
    assert set(out_cal) == set(CAL)
    assert set(out_news) == set(NEWS)
    assert set(out_crowd) == set(CROWD)
    want_cal = {"eq:A": -0.5, "eq:B": 0.5, "eq:C": 0.75, "eq:D": 1.0, "eq:E": 0.0, "eq:F": -1.0, "eq:G": 0.125,
                "eq:H": 0.125}  # fmt: skip
    want_news = {"eq:A": R2, "eq:B": RH, "eq:C": 1.25, "eq:E": 2.0, "eq:H": 1.5}
    want_crowd = {"eq:A": RH, "eq:B": R2, "eq:G": 1.0}
    for f, v in want_cal.items():
        # rel 1e-12 + abs 1e-15: spec tolerance; abs covers the zero-seed leg
        assert out_cal[f] == pytest.approx(v, rel=1e-12, abs=1e-15), f
    for f, v in want_news.items():
        assert out_news[f] == pytest.approx(v, rel=1e-12), f
    for f, v in want_crowd.items():
        assert out_crowd[f] == pytest.approx(v, rel=1e-12), f
    assert (seed, cal, news, crowd) == (SEED, CAL, NEWS, CROWD)  # inputs not mutated


@pytest.mark.criterion("C3")
def test_c3_point_default_cap():
    # oracle: Requirement 3 by hand, default cap 1.5:
    #   NVDA: seed -0.25, cal -0.375 -> a 1.5; n 1.5 -> m 2.25 = 1.5**2 -> lam 1/2: a, n -> sqrt 1.5,
    #         cal -0.25*sqrt 1.5
    #   AMD: seed 0.5, cal 0.25 -> a 0.5; c 0.75 -> m 0.375 < 2/3 -> lam = ln(2/3)/ln(0.375):
    #        cal 0.5 * 0.5**lam, c 0.75**lam
    #   TSM: seed -0.5, cal -0.625 -> a 1.25, inside, unchanged
    seed = {"eq:NVDA": -0.25, "eq:AMD": 0.5, "eq:TSM": -0.5}
    cal = {"eq:NVDA": -0.375, "eq:AMD": 0.25, "eq:TSM": -0.625}
    news = {"eq:NVDA": 1.5}
    crowd = {"eq:AMD": 0.75}
    out_cal, out_news, out_crowd = cap_leg_inputs(seed, cal, news, crowd)
    lam = math.log(1 / 1.5) / math.log(0.375)
    r15 = math.sqrt(1.5)
    assert set(out_cal) == set(cal)
    assert set(out_news) == {"eq:NVDA"}
    assert set(out_crowd) == {"eq:AMD"}
    # rel 1e-12: spec tolerance
    assert out_cal["eq:NVDA"] == pytest.approx(-0.25 * r15, rel=1e-12)
    assert out_news["eq:NVDA"] == pytest.approx(r15, rel=1e-12)
    assert out_cal["eq:AMD"] == pytest.approx(0.5 * 0.5**lam, rel=1e-12)
    assert out_crowd["eq:AMD"] == pytest.approx(0.75**lam, rel=1e-12)
    assert out_cal["eq:TSM"] == pytest.approx(-0.625, rel=1e-12)
    # the capped AMD compound sits on 1/1.5
    assert (out_cal["eq:AMD"] / 0.5) * out_crowd["eq:AMD"] == pytest.approx(1 / 1.5, rel=1e-12)


@pytest.mark.criterion("C3")
def test_c3_refusals():
    # oracle: Requirement 3: cap < 1, or seed and calibrated legs with different keys, raises ValueError.
    for cap in (0.99, 0.5, 0.0):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            cap_leg_inputs(dict(SEED), dict(CAL), dict(NEWS), dict(CROWD), cap=cap)
    short = {k: v for k, v in CAL.items() if k != "eq:H"}
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        cap_leg_inputs(dict(SEED), short, dict(NEWS), dict(CROWD))
    extra = {**CAL, "eq:Z": 0.5}
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        cap_leg_inputs(dict(SEED), extra, dict(NEWS), dict(CROWD))


_MULT = st.sampled_from([0.125, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 8.0])
_SEED = st.sampled_from([0.0, -0.5, -0.25, -0.125, 0.0625, 0.25, 1.0])
_CAP = st.sampled_from([1.0, 1.25, 1.5, 2.0, 3.0])


@st.composite
def _legs(draw: st.DrawFn):
    names = draw(st.lists(st.sampled_from([f"eq:N{i}" for i in range(8)]), min_size=1, max_size=8, unique=True))
    seed, cal, news, crowd = {}, {}, {}, {}
    for f in names:
        s = draw(_SEED)
        seed[f] = s
        cal[f] = s * draw(_MULT)
        if draw(st.booleans()):
            news[f] = draw(_MULT)
        if draw(st.booleans()):
            crowd[f] = draw(_MULT)
    return seed, cal, news, crowd


def _factors(seed: dict, cal: dict, news: dict, crowd: dict, f: str) -> tuple[float, float, float]:
    a = 1.0 if not seed[f] else cal[f] / seed[f]
    return a, news.get(f, 1.0), crowd.get(f, 1.0)


@pytest.mark.criterion("C3")
@given(legs=_legs(), cap=_CAP)
def test_c3_property_compound_in_band_shares_and_keys(legs, cap: float):
    # oracle: C3 properties: after capping every compound lies in [1/cap, cap] (1e-12); each factor keeps its
    # share of ln m (ln x' * ln m = ln x * ln m'); legs inside the band are unchanged; keys and inputs unchanged.
    seed, cal, news, crowd = legs
    before = copy.deepcopy(legs)
    out_cal, out_news, out_crowd = cap_leg_inputs(seed, cal, news, crowd, cap=cap)
    assert (seed, cal, news, crowd) == before
    assert set(out_cal) == set(cal)
    assert set(out_news) == set(news)
    assert set(out_crowd) == set(crowd)
    for f in seed:
        x = _factors(seed, cal, news, crowd, f)
        m = math.prod(x)
        y = _factors(seed, out_cal, out_news, out_crowd, f)
        m2 = math.prod(y)
        # 1e-12: spec tolerance on the band
        assert 1 / cap - 1e-12 <= m2 <= cap + 1e-12, f
        if 1 / cap <= m <= cap:
            # rel 1e-12 + abs 1e-15: spec tolerance; abs covers zero moves
            assert out_cal[f] == pytest.approx(cal[f], rel=1e-12, abs=1e-15), f
            assert y == pytest.approx(x, rel=1e-12), f
        else:
            # control: a capped compound lands on the bound it crossed
            assert m2 == pytest.approx(cap if m > cap else 1 / cap, rel=1e-12), f
            for xi, yi in zip(x, y, strict=True):
                # abs 1e-12: shares of ln m, with logs of at most ln 8**3 in size
                assert math.log(yi) * math.log(m) == pytest.approx(math.log(xi) * math.log(m2), abs=1e-12), f


@pytest.mark.criterion("C3")
@given(legs=_legs(), cap=_CAP)
def test_c3_property_matches_power_formula(legs, cap: float):
    # oracle: Requirement 3 computed in the test: x -> x**lam per factor, calibrated -> seed * a**lam.
    seed, cal, news, crowd = legs
    out_cal, out_news, out_crowd = cap_leg_inputs(seed, cal, news, crowd, cap=cap)
    for f in seed:
        a, n, c = _factors(seed, cal, news, crowd, f)
        m = a * n * c
        if m > cap:
            lam = math.log(cap) / math.log(m)
        elif m < 1 / cap:
            lam = math.log(1 / cap) / math.log(m)
        else:
            lam = 1.0  # inside the band: unchanged
        want_cal = cal[f] if 1 / cap <= m <= cap else seed[f] * a**lam
        # rel 1e-12 + abs 1e-15: spec tolerance; abs covers zero seeds
        assert out_cal[f] == pytest.approx(want_cal, rel=1e-12, abs=1e-15), f
        if f in news:
            assert out_news[f] == pytest.approx(n**lam, rel=1e-12), f
        if f in crowd:
            assert out_crowd[f] == pytest.approx(c**lam, rel=1e-12), f
