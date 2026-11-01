"""long-history-ingest C4: per-factor long move history with a proxy spliced before the own start.

Oracle: Requirement 5 by hand. Moves are computed in the test from the levels with Requirement 4's
formula (``L_t / L_{t-1} - 1`` for ``eq:``/``fx:``, ``(L_t - L_{t-1}) / 100`` otherwise);
``beta = std(own, ddof=1) / std(proxy, ddof=1)`` over the shared move dates with numpy. Invariances (own
moves untouched by a splice; proxy prices scaled by a constant) are properties. Data are synthetic.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.scenariogen.long_history import build_long_history

DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2001-01-01", periods=10)]
PROXY = [100.0, 102.0, 99.0, 101.0, 104.0, 103.0, 106.0, 105.0, 108.0, 110.0]  # ^SOX, all 10 dates
OWN = [50.0, 52.0, 51.0, 55.0, 53.0, 57.0]  # AVGO, dates 4..9 -> own moves on 5..9
NVDA = [10.0, 11.0, 10.5, 10.0, 12.0, 12.5, 12.0, 13.0, 12.5, 14.0]
XLU = [30.0, 30.5, 30.25, 31.0, 30.0, 30.5, 31.5, 31.0, 32.0, 31.75]
DGS10 = [5.1, 5.05, 5.2, 5.12, 5.0, 4.9, 4.95, 5.02, 5.1, 5.07]  # percent
VIX = [25.0, 26.5, 24.0, 30.0, 31.0, 28.5, 27.0, 29.0, 33.0, 32.0]  # index points


def _rets(levels: list[float]) -> list[float]:
    return [b / a - 1.0 for a, b in pairwise(levels)]


def _diffs(levels: list[float]) -> list[float]:
    return [(b - a) / 100.0 for a, b in pairwise(levels)]


def _prices(**tickers: tuple[list[str], list[float]]) -> pd.DataFrame:
    rows = [(d, t, v) for t, (ds, vs) in tickers.items() for d, v in zip(ds, vs, strict=True)]
    df = pd.DataFrame(rows, columns=["date", "ticker", "adj_close"])
    return df.sort_values(["date", "ticker"], kind="stable").reset_index(drop=True)


def _fred() -> pd.DataFrame:
    return pd.DataFrame({"date": DATES, "DGS10": DGS10, "VIXCLS": VIX})


def _pairs(s: pd.Series) -> list[tuple[str, float]]:
    return [(pd.Timestamp(k).strftime("%Y-%m-%d"), float(v)) for k, v in s.items()]


def _norm(d: object) -> str | None:
    return None if d is None else pd.Timestamp(d).strftime("%Y-%m-%d")


def _book_prices() -> pd.DataFrame:
    return _prices(
        **{"^SOX": (DATES, PROXY), "AVGO": (DATES[4:], OWN), "NVDA": (DATES, NVDA), "XLU": (DATES, XLU)}
    )


MAPPING = {
    "eq:AVGO": {"ticker": "AVGO", "proxy": "^SOX"},
    "eq:NVDA": {"ticker": "NVDA"},
    "eq:CEG": {"ticker": "CEG", "proxy": "XLU"},
    "eq:GHOST": {"ticker": "GHOST", "proxy": "NOPROXY"},
    "eq:GLD": {"ticker": "GLD"},
    "ir:DGS10": {"fred": "DGS10"},
    "eqvol": {"fred": "VIXCLS"},
}


@pytest.mark.criterion("C4")
def test_c4_point_splice_with_beta():
    # oracle: Requirement 5 by hand: own AVGO moves on dates 5..9, proxy ^SOX moves on 1..9; shared = 5 dates
    # >= min_overlap 5, so beta = std(own) / std(proxy[5..9]) (ddof 1) and proxy moves on 1..4 times beta are
    # prepended to the unchanged own moves.
    series, info = build_long_history(_book_prices(), _fred(), MAPPING, min_overlap=5)
    own = _rets(OWN)
    prx = _rets(PROXY)  # moves on dates 1..9
    beta = float(np.std(own, ddof=1) / np.std(prx[4:], ddof=1))
    expected = [(DATES[k + 1], prx[k] * beta) for k in range(4)] + [(DATES[k + 5], own[k]) for k in range(5)]
    got = _pairs(series["eq:AVGO"])
    assert [d for d, _ in got] == [d for d, _ in expected]
    # rel 1e-12: spec tolerance
    assert [v for _, v in got] == pytest.approx([v for _, v in expected], rel=1e-12)
    i = info["eq:AVGO"]
    assert i["source"] == "AVGO"
    assert i["proxy"] == "^SOX"
    assert i["beta"] == pytest.approx(beta, rel=1e-12)  # rel 1e-12: spec tolerance
    assert i["spliced"] is True
    assert i["missing"] is False
    assert _norm(i["own_start"]) == DATES[5]
    assert _norm(i["start"]) == DATES[1]


@pytest.mark.criterion("C4")
def test_c4_point_below_overlap_no_splice():
    # oracle: Requirement 5: 5 shared dates < min_overlap 6 -> no splice: own moves only, beta None.
    series, info = build_long_history(_book_prices(), _fred(), MAPPING, min_overlap=6)
    got = _pairs(series["eq:AVGO"])
    assert [d for d, _ in got] == DATES[5:]
    assert [v for _, v in got] == pytest.approx(_rets(OWN), rel=1e-12)  # rel 1e-12: spec tolerance
    i = info["eq:AVGO"]
    assert i["beta"] is None
    assert i["spliced"] is False
    assert i["missing"] is False
    assert i["proxy"] == "^SOX"
    assert _norm(i["own_start"]) == DATES[5]
    assert _norm(i["start"]) == DATES[5]


@pytest.mark.criterion("C4")
def test_c4_point_fallback_missing_own_and_fred_factors():
    # oracle: Requirement 5 by hand:
    #   eq:NVDA own ticker, no proxy -> NVDA returns on dates 1..9, beta None, not spliced;
    #   eq:CEG own ticker absent -> XLU returns with beta 1, spliced, missing, own_start None;
    #   eq:GHOST / eq:GLD no data at all -> left out of series; info missing, start None;
    #   ir:DGS10 / eqvol from the FRED columns -> percent-point changes / 100.
    series, info = build_long_history(_book_prices(), _fred(), MAPPING, min_overlap=5)
    assert set(series) == {"eq:AVGO", "eq:NVDA", "eq:CEG", "ir:DGS10", "eqvol"}
    for key, want in (
        ("eq:NVDA", _rets(NVDA)),
        ("eq:CEG", _rets(XLU)),
        ("ir:DGS10", _diffs(DGS10)),
        ("eqvol", _diffs(VIX)),
    ):
        got = _pairs(series[key])
        assert [d for d, _ in got] == DATES[1:]
        assert [v for _, v in got] == pytest.approx(want, rel=1e-12)  # rel 1e-12: spec tolerance
    assert info["eq:NVDA"]["source"] == "NVDA"
    assert info["eq:NVDA"]["proxy"] is None
    assert info["eq:NVDA"]["beta"] is None
    assert info["eq:NVDA"]["spliced"] is False
    assert info["eq:NVDA"]["missing"] is False
    assert _norm(info["eq:NVDA"]["own_start"]) == DATES[1]
    assert _norm(info["eq:NVDA"]["start"]) == DATES[1]
    ceg = info["eq:CEG"]
    assert (ceg["source"], ceg["proxy"], ceg["spliced"], ceg["missing"]) == ("CEG", "XLU", True, True)
    assert ceg["beta"] == pytest.approx(1.0, rel=1e-12)  # rel 1e-12: spec tolerance
    assert ceg["own_start"] is None
    assert _norm(ceg["start"]) == DATES[1]
    for key in ("eq:GHOST", "eq:GLD"):
        g = info[key]
        assert g["missing"] is True
        assert g["spliced"] is False
        assert g["beta"] is None
        assert g["own_start"] is None
        assert g["start"] is None
    for key, col in (("ir:DGS10", "DGS10"), ("eqvol", "VIXCLS")):
        f = info[key]
        assert (f["source"], f["proxy"], f["beta"], f["spliced"], f["missing"]) == (col, None, None, False, False)
        assert _norm(f["own_start"]) == DATES[1]
        assert _norm(f["start"]) == DATES[1]


_LVL = st.integers(min_value=40, max_value=400).map(lambda i: i / 4)


@st.composite
def _splice_case(draw: st.DrawFn):
    n = draw(st.integers(min_value=5, max_value=40))
    k0 = draw(st.integers(min_value=1, max_value=n - 3))  # own levels start at date index k0 >= 1
    proxy = draw(st.lists(_LVL, min_size=n, max_size=n))
    own = draw(st.lists(_LVL, min_size=n - k0, max_size=n - k0))
    shared = n - k0 - 1
    min_overlap = draw(st.integers(min_value=2, max_value=shared + 2))
    return n, k0, proxy, own, min_overlap


@pytest.mark.criterion("C4")
@given(case=_splice_case())
def test_c4_property_splice_matches_requirement(case):
    # oracle: Requirement 5 computed in the test with numpy std(ddof=1); own moves never altered.
    n, k0, proxy, own, min_overlap = case
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("1995-01-02", periods=n)]
    own_m = _rets(own)  # on dates k0+1 .. n-1
    prx_m = _rets(proxy)  # on dates 1 .. n-1
    shared_prx = prx_m[k0:]
    assume(len(shared_prx) >= 2 and np.std(shared_prx, ddof=1) > 0)
    prices = _prices(P=(dates, proxy), O=(dates[k0:], own))
    series, info = build_long_history(prices, pd.DataFrame({"date": dates}), {"eq:O": {"ticker": "O", "proxy": "P"}},
                                      min_overlap=min_overlap)
    got = _pairs(series["eq:O"])
    own_part = [(dates[k0 + 1 + j], own_m[j]) for j in range(len(own_m))]
    if len(own_m) >= min_overlap:
        beta = float(np.std(own_m, ddof=1) / np.std(shared_prx, ddof=1))
        pre = [(dates[1 + j], prx_m[j] * beta) for j in range(k0)]
        assert info["eq:O"]["beta"] == pytest.approx(beta, rel=1e-12)  # rel 1e-12: spec tolerance
        assert info["eq:O"]["spliced"] is True
    else:
        pre = []
        assert info["eq:O"]["beta"] is None
        assert info["eq:O"]["spliced"] is False
    expected = pre + own_part
    assert [d for d, _ in got] == [d for d, _ in expected]
    # rel 1e-12 + abs 1e-15: spec tolerance; abs covers zero moves
    assert [v for _, v in got] == pytest.approx([v for _, v in expected], rel=1e-12, abs=1e-15)
    # own moves are exactly the own moves, untouched by the splice
    assert [v for _, v in got[len(pre):]] == pytest.approx(own_m, rel=1e-12, abs=1e-15)
    assert _norm(info["eq:O"]["own_start"]) == dates[k0 + 1]
    assert _norm(info["eq:O"]["start"]) == expected[0][0]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(case=_splice_case(), c=st.integers(min_value=1, max_value=4000).map(lambda i: i / 16))
def test_c4_metamorphic_proxy_price_scale_invariance(case, c: float):
    # oracle: Requirement 5: beta and the spliced series depend on proxy *returns*, which multiplying the proxy's
    # prices by c > 0 leaves unchanged.
    n, k0, proxy, own, min_overlap = case
    assume(np.std(_rets(proxy)[k0:], ddof=1) > 0)  # k0 <= n - 3 leaves >= 2 shared moves
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("1995-01-02", periods=n)]
    mapping = {"eq:O": {"ticker": "O", "proxy": "P"}}
    fred = pd.DataFrame({"date": dates})
    s1, i1 = build_long_history(_prices(P=(dates, proxy), O=(dates[k0:], own)), fred, mapping,
                                min_overlap=min_overlap)
    s2, i2 = build_long_history(_prices(P=(dates, [c * p for p in proxy]), O=(dates[k0:], own)), fred,
                                mapping, min_overlap=min_overlap)
    a, b = _pairs(s1["eq:O"]), _pairs(s2["eq:O"])
    assert len(a) >= 2
    assert [d for d, _ in a] == [d for d, _ in b]
    # rel 1e-9 + abs 1e-12: cP_t / cP_{t-1} differs from P_t / P_{t-1} by float rounding only
    assert [v for _, v in b] == pytest.approx([v for _, v in a], rel=1e-9, abs=1e-12)
    assert i1["eq:O"]["spliced"] == i2["eq:O"]["spliced"]
    if i1["eq:O"]["beta"] is None:
        assert i2["eq:O"]["beta"] is None
    else:
        assert i2["eq:O"]["beta"] == pytest.approx(i1["eq:O"]["beta"], rel=1e-9)  # rel 1e-9: as above
