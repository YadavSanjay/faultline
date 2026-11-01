"""scenario-historical-replay C4: scaling a proxy series to a target's volatility.

Oracle: Requirement 4 by hand: ``beta = std(target) / std(proxy)`` with ddof 1 over the dates the two series
share (compared as calendar dates), and the whole proxy series is multiplied by ``beta``. Properties: the scaled
proxy's deviation over the shared dates equals the target's; scaling the target by ``c > 0`` scales ``beta`` by
``c``.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.scenariogen.replay import vol_matched

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10"]


def _pairs(s: pd.Series) -> list[tuple[str, float]]:
    return [(pd.Timestamp(k).strftime("%Y-%m-%d"), float(v)) for k, v in s.items()]


@pytest.mark.criterion("C4")
def test_c4_point_proportional_target_gives_beta_two():
    # oracle: Requirement 4 by hand: on the shared dates 01-03..01-08 the target is exactly 2 x the proxy, so
    # beta = 2; the whole proxy (01-02..01-09, including the unshared ends) is doubled. The target's own
    # unshared 01-10 row plays no part. The proxy is string-indexed, the target Timestamp-indexed.
    proxy = pd.Series([1.0, 0.5, -0.5, 0.25, -0.25, 0.125], index=DAYS[:6])
    target = pd.Series([1.0, -1.0, 0.5, -0.5, 0.75], index=pd.DatetimeIndex([*DAYS[1:5], DAYS[6]]))
    scaled, beta = vol_matched(proxy, target, min_overlap=4)
    # rel 1e-12: spec tolerance (a square root of an exact ratio)
    assert beta == pytest.approx(2.0, rel=1e-12)
    got = _pairs(scaled)
    assert [d for d, _ in got] == DAYS[:6]
    assert [v for _, v in got] == pytest.approx([2.0, 1.0, -1.0, 0.5, -0.5, 0.25], rel=1e-12)


@pytest.mark.criterion("C4")
def test_c4_point_ddof_one_ratio():
    # oracle: Requirement 4 by hand, cross-checked with numpy std(ddof=1):
    #   proxy on shared dates [0.5, -0.25, 0.125, 0.0]: mean 0.09375, squared deviations sum 0.29296875,
    #     variance 0.29296875 / 3 = 0.09765625;
    #   target [0.25, 0.25, -0.5, 0.0]: mean 0, squares sum 0.375, variance 0.125;
    #   beta = sqrt(0.125 / 0.09765625) = sqrt(1.28).
    # Index types: proxy date objects, target strings; the proxy's 01-02 row and 01-09 row are not shared.
    proxy = pd.Series(
        [0.0625, 0.5, -0.25, 0.125, 0.0, -1.0], index=[date.fromisoformat(d) for d in DAYS[:6]]
    )
    target = pd.Series([0.25, 0.25, -0.5, 0.0], index=DAYS[1:5])
    want = math.sqrt(1.28)
    p_sh = np.array([0.5, -0.25, 0.125, 0.0])
    t_sh = np.array([0.25, 0.25, -0.5, 0.0])
    # rel 1e-12: spec tolerance
    assert float(t_sh.std(ddof=1) / p_sh.std(ddof=1)) == pytest.approx(want, rel=1e-12)
    scaled, beta = vol_matched(proxy, target, min_overlap=4)
    assert beta == pytest.approx(want, rel=1e-12)
    got = _pairs(scaled)
    assert [d for d, _ in got] == DAYS[:6]
    assert [v for _, v in got] == pytest.approx([x * want for x in proxy.to_numpy()], rel=1e-12, abs=1e-15)


def _bdays(n: int) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2020-01-01", periods=n)]


@pytest.mark.criterion("C4")
def test_c4_refusals_and_default_min_overlap():
    # oracle: Requirement 4: fewer than min_overlap shared dates (default 250) raises ValueError; exactly 250
    # does not. A proxy constant over the shared dates (sigma_proxy = 0) raises even if it varies elsewhere.
    days = _bdays(300)
    proxy = pd.Series([0.125 if k % 2 else -0.0625 for k in range(300)], index=days)
    target_249 = pd.Series([0.25 if k % 3 else -0.5 for k in range(249)], index=pd.DatetimeIndex(days[51:]))
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        vol_matched(proxy, target_249)
    target_250 = pd.Series([0.25 if k % 3 else -0.5 for k in range(250)], index=pd.DatetimeIndex(days[50:]))
    scaled, beta = vol_matched(proxy, target_250)
    assert beta > 0
    assert len(scaled) == 300
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        vol_matched(proxy, target_250, min_overlap=251)
    flat = pd.Series([0.0625] * 8 + [0.5, -0.5], index=_bdays(8) + DAYS[:2])
    target = pd.Series([0.25, -0.25, 0.5, 0.0, 0.125, -0.5, 0.25, 0.0], index=_bdays(8))
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        vol_matched(flat, target, min_overlap=4)


_V = st.integers(min_value=-64, max_value=64).map(lambda i: i / 64)


@st.composite
def _pair(draw: st.DrawFn) -> tuple[pd.Series, pd.Series, list[str]]:
    shared = draw(st.lists(st.tuples(_V, _V), min_size=3, max_size=30))
    extra_p = draw(st.lists(_V, max_size=5))
    extra_t = draw(st.lists(_V, max_size=5))
    p_sh = [p for p, _ in shared]
    t_sh = [t for _, t in shared]
    assume(len(set(p_sh)) > 1 and len(set(t_sh)) > 1)
    first = date(2015, 3, 2)
    n = len(shared)
    shared_days = [(first + timedelta(days=k)).isoformat() for k in range(n)]
    p_days = [(first - timedelta(days=k + 1)).isoformat() for k in range(len(extra_p))][::-1] + shared_days
    t_days = shared_days + [(first + timedelta(days=n + k)).isoformat() for k in range(len(extra_t))]
    kind = draw(st.sampled_from(["str", "ts", "date"]))

    def _index(days: list[str]) -> object:
        if kind == "str":
            return days
        if kind == "ts":
            return pd.DatetimeIndex(days)
        return [date.fromisoformat(d) for d in days]

    proxy = pd.Series([*extra_p, *p_sh], index=_index(p_days))
    target = pd.Series([*t_sh, *extra_t], index=pd.DatetimeIndex(t_days) if kind == "str" else t_days)
    return proxy, target, shared_days


@pytest.mark.criterion("C4")
@given(case=_pair())
def test_c4_property_scaled_deviation_matches_target(case: tuple[pd.Series, pd.Series, list[str]]):
    # oracle: C4 property: std(proxy x beta) over the shared dates = std(target) there (numpy, ddof 1); the
    # scaled series is the whole proxy times beta.
    proxy, target, shared_days = case
    n = len(shared_days)
    scaled, beta = vol_matched(proxy, target, min_overlap=n)
    got = _pairs(scaled)
    assert [d for d, _ in got] == [d for d, _ in _pairs(proxy)]
    # rel 1e-12 + abs 1e-15: spec tolerance; abs covers zero moves
    assert [v for _, v in got] == pytest.approx([v * beta for _, v in _pairs(proxy)], rel=1e-12, abs=1e-15)
    scaled_shared = np.array([v for d, v in got if d in set(shared_days)])
    target_shared = np.array([v for d, v in _pairs(target) if d in set(shared_days)])
    assert len(scaled_shared) == n
    assert float(scaled_shared.std(ddof=1)) == pytest.approx(float(target_shared.std(ddof=1)), rel=1e-12)
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        vol_matched(proxy, target, min_overlap=n + 1)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(case=_pair(), c=st.sampled_from([0.25, 0.5, 2.0, 3.0, 8.0]))
def test_c4_metamorphic_target_scaling_scales_beta(case: tuple[pd.Series, pd.Series, list[str]], c: float):
    # oracle: C4 property: std(c x target) = c x std(target) for c > 0, so beta(c x target) = c x beta(target);
    # beta itself matches std(target) / std(proxy) over the shared dates computed with numpy (ddof 1).
    proxy, target, shared_days = case
    n = len(shared_days)
    _, beta = vol_matched(proxy, target, min_overlap=n)
    _, beta_c = vol_matched(proxy, target * c, min_overlap=n)
    p_sh = np.array([v for d, v in _pairs(proxy) if d in set(shared_days)])
    t_sh = np.array([v for d, v in _pairs(target) if d in set(shared_days)])
    # rel 1e-12: spec tolerance
    assert beta == pytest.approx(float(t_sh.std(ddof=1) / p_sh.std(ddof=1)), rel=1e-12)
    assert beta_c == pytest.approx(c * beta, rel=1e-12)
