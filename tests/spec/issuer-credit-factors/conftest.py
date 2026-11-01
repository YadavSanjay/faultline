"""Hand-calculation helpers and synthetic fixtures for the issuer-credit-factors spec tests.

Everything is synthetic (docs/specs/issuer-credit-factors.md, Notes). ``hand_daily`` is Requirement 1
written out step by step in plain Python (``statistics.median``), used as the oracle of the C1
property tests; ``hand_moves`` is Requirement 2 written out the same way. Neither calls the code under
test. Items for C4 are real frozen ``CreditSpreadData`` / ``CdsSpreadData`` instances.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import fields
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from src.pricer.input.common.market_data import CdsSpreadData, CreditSpreadData, EquitySpotData

AS_OF = date(2026, 10, 6)


def ts(d: str) -> pd.Timestamp:
    """ISO date string -> pd.Timestamp."""
    return pd.Timestamp(d)


def frame(rows: Sequence[tuple[object, float, float]]) -> pd.DataFrame:
    """Prints frame from (date, spread, tenor_years) rows."""
    return pd.DataFrame(list(rows), columns=["date", "spread", "tenor_years"])


def series(pairs: Sequence[tuple[str, float]]) -> pd.Series:
    """Float series indexed by pd.Timestamp from (ISO date, value) pairs."""
    return pd.Series([v for _, v in pairs], index=pd.DatetimeIndex([ts(d) for d, _ in pairs]), dtype=float)


def empty_series() -> pd.Series:
    return pd.Series([], index=pd.DatetimeIndex([]), dtype=float)


def hand_daily(rows: Sequence[tuple[object, float, float]], lo: float, hi: float, ratio: float,
               lookback: int) -> list[tuple[pd.Timestamp, float]]:
    """Requirement 1 by hand: bucket + positive filter, median per date, sequential outlier walk."""
    by_date: dict[pd.Timestamp, list[float]] = {}
    for d, s, t in rows:
        if lo <= t <= hi and s > 0:
            by_date.setdefault(pd.Timestamp(d), []).append(s)
    kept: list[tuple[pd.Timestamp, float]] = []
    for d in sorted(by_date):
        m = statistics.median(by_date[d])
        if len(kept) >= 3:
            ref = statistics.median([v for _, v in kept[-lookback:]])
            if m < ref / ratio or m > ref * ratio:
                continue
        kept.append((d, m))
    return kept


def hand_moves(daily: Sequence[tuple[pd.Timestamp, float]], calendar: Sequence[pd.Timestamp],
               max_gap: int) -> list[tuple[pd.Timestamp, float]]:
    """Requirement 2 by hand: restrict, place, forward-fill at most max_gap dates, difference."""
    if not daily:
        return []
    first, last = daily[0][0], daily[-1][0]
    vals = dict(daily)
    cal = [c for c in calendar if first <= c <= last]
    filled: list[float | None] = []
    cur: float | None = None
    run = 0
    for c in cal:
        if c in vals:
            cur, run = vals[c], 0
            filled.append(cur)
        elif cur is not None and run < max_gap:
            run += 1
            filled.append(cur)
        else:
            cur = None
            filled.append(None)
    out = []
    for i in range(1, len(cal)):
        a, b = filled[i - 1], filled[i]
        if a is not None and b is not None:
            out.append((cal[i], b - a))
    return out


def assert_series(got: pd.Series, want: Sequence[tuple[pd.Timestamp, float]], rel: float) -> None:
    """Same ascending Timestamp index, values within rel (exact zeros must be exact)."""
    assert isinstance(got, pd.Series)
    assert [pd.Timestamp(i) for i in got.index] == [d for d, _ in want]
    assert [float(v) for v in got.to_numpy()] == pytest.approx([v for _, v in want], rel=rel)


def make_items() -> dict[str, object]:
    """C4 items: bonds and CDS with and without curve points, an index CDS and two unrelated items."""
    return {
        "bond_orcl": CreditSpreadData(issuer="ORCL", currency="USD", spread=0.0240, as_of_date=AS_OF,
                                      from_cds=True, source="dtcc",
                                      curve_points=((1.0, 0.0150), (5.0, 0.0240), (10.0, 0.0280))),
        "bond_aapl": CreditSpreadData(issuer="AAPL", currency="USD", spread=0.0045, as_of_date=AS_OF),
        "bond_meta": CreditSpreadData(issuer="META", currency="USD", spread=0.0096, as_of_date=AS_OF),
        "cds_meta": CdsSpreadData(reference="META", currency="USD", spread=0.0096, recovery=0.4,
                                  as_of_date=AS_OF, from_dtcc=True, source="dtcc",
                                  curve_points=((3.0, 0.0080), (5.0, 0.0096), (7.0, 0.0110))),
        "cds_orcl": CdsSpreadData(reference="ORCL", currency="USD", spread=0.0246, recovery=0.4,
                                  as_of_date=AS_OF),
        "cds_cdx": CdsSpreadData(reference="CDX.NA.IG", currency="USD", spread=0.0055, recovery=0.4,
                                 as_of_date=AS_OF, n_constituents=125),
        "spot_orcl": EquitySpotData(ticker="ORCL", currency="USD", spot=180.0, as_of_date=AS_OF),
        "plain": "credit:ORCL",
    }


def name_of(item: object) -> str | None:
    if isinstance(item, CreditSpreadData):
        return item.issuer
    if isinstance(item, CdsSpreadData):
        return item.reference
    return None


def assert_shifted(got: object, base: object, d: float, rel: float) -> None:
    """``got`` is ``base`` with ``d`` added to spread and every curve-point spread; nothing else moved."""
    assert type(got) is type(base)
    for f in fields(base):  # type: ignore[arg-type]  # base is a dataclass instance here
        g, b = getattr(got, f.name), getattr(base, f.name)
        if f.name == "spread":
            assert g == pytest.approx(b + d, rel=rel, abs=1e-15)  # spec rel 1e-12; abs for a ~0 sum
        elif f.name == "curve_points":
            assert [t for t, _ in g] == [t for t, _ in b]
            assert [s for _, s in g] == pytest.approx([s + d for _, s in b], rel=rel, abs=1e-15)  # spec rel
        else:
            assert g == b


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        AS_OF=AS_OF, ts=ts, frame=frame, series=series, empty_series=empty_series, hand_daily=hand_daily,
        hand_moves=hand_moves, assert_series=assert_series, make_items=make_items, name_of=name_of,
        assert_shifted=assert_shifted,
    )
