"""issuer-credit-factors C3: ``proportional_backfill``, issuer moves extended backwards with the proxy.

Oracle: Requirement 3 by hand: before the first date of ``moves`` the result is ``proxy_moves * beta``
with ``beta = issuer_level / proxy_level``; from that date on it is ``moves`` unchanged; sorted by date;
empty ``moves`` gives ``proxy_moves * beta``.
"""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.risk.issuer_credit import proportional_backfill

NAN = float("nan")
INF = float("inf")

PROXY = [("2026-03-02", 0.0010), ("2026-03-03", -0.0020), ("2026-03-04", 0.0005), ("2026-03-05", 0.0030),
         ("2026-03-06", -0.0040), ("2026-03-09", 0.0007), ("2026-03-10", 0.0001)]
ISSUER = [("2026-03-05", 0.0100), ("2026-03-09", -0.0050), ("2026-03-10", 0.0020)]


@pytest.mark.criterion("C3")
def test_c3_point_splice(h):
    # oracle: hand calculation, beta = 0.0036 / 0.0086 (spec Notes ORCL-like splice, synthetic levels):
    #   03-02 .0010*beta, 03-03 -.0020*beta, 03-04 .0005*beta (proxy dates before the first issuer date 03-05);
    #   from 03-05 on the issuer moves .0100, -.0050, .0020; the proxy's 03-05 / 03-06 / 03-09 / 03-10 values
    #   are not used (03-06 is a proxy-only date after the splice -> absent).
    beta = 0.0036 / 0.0086
    got = proportional_backfill(h.series(ISSUER), h.series(PROXY), issuer_level=0.0036, proxy_level=0.0086)
    want = [(h.ts("2026-03-02"), 0.0010 * beta), (h.ts("2026-03-03"), -0.0020 * beta),
            (h.ts("2026-03-04"), 0.0005 * beta),
            (h.ts("2026-03-05"), 0.0100), (h.ts("2026-03-09"), -0.0050), (h.ts("2026-03-10"), 0.0020)]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C3")
def test_c3_point_unsorted_inputs_give_sorted_result(h):
    # oracle: hand calculation: same data as the splice case with both inputs given in reverse order;
    #   the result is sorted by date and holds the same values (beta = 0.0068 / 0.0081, META-like).
    beta = 0.0068 / 0.0081
    issuer = h.series(ISSUER[::-1])
    proxy = h.series(PROXY[::-1])
    got = proportional_backfill(issuer, proxy, issuer_level=0.0068, proxy_level=0.0081)
    want = [(h.ts("2026-03-02"), 0.0010 * beta), (h.ts("2026-03-03"), -0.0020 * beta),
            (h.ts("2026-03-04"), 0.0005 * beta),
            (h.ts("2026-03-05"), 0.0100), (h.ts("2026-03-09"), -0.0050), (h.ts("2026-03-10"), 0.0020)]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C3")
def test_c3_point_empty_moves_is_scaled_proxy(h):
    # oracle: Requirement 3: empty moves -> proxy_moves * beta on every proxy date; beta = 0.02 / 0.01 = 2.
    got = proportional_backfill(h.empty_series(), h.series(PROXY), issuer_level=0.02, proxy_level=0.01)
    want = [(h.ts(d), v * 2.0) for d, v in PROXY]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("issuer_level", "proxy_level"),
    [(0.0, 0.0086), (-0.0036, 0.0086), (NAN, 0.0086), (INF, 0.0086),
     (0.0036, 0.0), (0.0036, -0.0086), (0.0036, NAN), (0.0036, INF)],
)
def test_c3_point_bad_levels_raise(h, issuer_level, proxy_level):
    # oracle: Requirement 3: a non-positive or non-finite level raises ValueError.
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        proportional_backfill(h.series(ISSUER), h.series(PROXY), issuer_level=issuer_level,
                              proxy_level=proxy_level)


_BDAYS = list(pd.bdate_range("2026-01-05", periods=30))
_move = st.one_of(st.floats(min_value=1e-5, max_value=0.01), st.floats(min_value=-0.01, max_value=-1e-5))
_level = st.floats(min_value=0.001, max_value=0.05)


@st.composite
def _case(draw):
    split = draw(st.integers(min_value=1, max_value=len(_BDAYS) - 1))
    proxy_vals = draw(st.lists(_move, min_size=len(_BDAYS), max_size=len(_BDAYS)))
    issuer_days = [d for d in _BDAYS[split:] if draw(st.booleans())] or [_BDAYS[split]]
    issuer_vals = draw(st.lists(_move, min_size=len(issuer_days), max_size=len(issuer_days)))
    proxy = pd.Series(proxy_vals, index=pd.DatetimeIndex(_BDAYS), dtype=float)
    moves = pd.Series(issuer_vals, index=pd.DatetimeIndex(issuer_days), dtype=float)
    return moves, proxy


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(case=_case(), il=_level, pl=_level, k=st.floats(min_value=0.01, max_value=100.0))
def test_c3_metamorphic_scaling_both_levels_changes_nothing(h, case, il, pl, k):
    # oracle: Requirement 3: beta = (k*il)/(k*pl) = il/pl, so the result is unchanged; both equal the hand
    #   splice: proxy*il/pl before the first issuer date, issuer moves from it on.
    moves, proxy = case
    first = moves.index.min()
    want = [(d, float(v) * (il / pl)) for d, v in proxy.items() if d < first] + list(moves.items())
    a = proportional_backfill(moves, proxy, issuer_level=il, proxy_level=pl)
    b = proportional_backfill(moves, proxy, issuer_level=il * k, proxy_level=pl * k)
    h.assert_series(a, want, rel=1e-12)  # spec rel 1e-12
    h.assert_series(b, want, rel=1e-12)  # spec rel 1e-12 (k*il/(k*pl) differs from il/pl by ~1 ulp)


@pytest.mark.criterion("C3")
@given(case=_case(), il=_level, pl=_level)
def test_c3_property_issuer_dates_never_altered(h, case, il, pl):
    # oracle: Requirement 3: dates of moves keep their own values exactly (no arithmetic touches them).
    moves, proxy = case
    got = proportional_backfill(moves, proxy, issuer_level=il, proxy_level=pl)
    assert [float(got.loc[d]) for d in moves.index] == [float(v) for v in moves.to_numpy()]
    assert all(d < moves.index.min() for d in got.index if d not in moves.index)
