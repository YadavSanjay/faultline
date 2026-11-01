"""long-history-ingest C2: Yahoo and FRED long-history ``collect()`` with a fake HTTP client.

Oracle: Requirements 2-3 by hand. The fake client is the HTTP boundary of the spec's client contract:
``get(endpoint)`` returns an object with ``.json()`` and ``.text``, or raises ``HttpClientError``. It
routes by the Yahoo symbol in the URL path (URL-decoded) or the FRED ``id`` query parameter and records
every endpoint. ``run()`` is never called (it writes under data/). Configs are built in the tests.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.common.errors import MarketDataIngestError
from src.marketdata.common.http_client import HttpClientError
from src.marketdata.equity.yahoo_history_downloader import YahooHistoryDownloader
from src.marketdata.stress.fred_long_downloader import FredLongDownloader, parse_fredgraph

YAHOO_TEMPLATE = "https://yahoo.test/v8/finance/chart/{yahoo_ticker}?period1={period1}&period2={period2}&interval=1d"
FRED_TEMPLATE = "https://fred.test/graph/fredgraph.csv?id={series_id}"
# 1990-01-01 00:00 UTC: 20 years after 1970 with 5 leap days (72, 76, 80, 84, 88) = 7305 days * 86400.
PERIOD1_1990 = 631152000
NY = -18000  # gmtoffset, seconds


class _Resp:
    def __init__(self, payload: object = None, text: str = "") -> None:
        self._payload = payload
        self.text = text

    def json(self) -> object:
        return self._payload


class _FakeClient:
    """Routes by key; a value that is an exception is raised."""

    def __init__(self, routes: dict[str, object], key: str) -> None:
        self.routes = routes
        self.key = key
        self.calls: list[str] = []

    def keys_called(self) -> list[str]:
        return [self._key(e) for e in self.calls]

    def _key(self, endpoint: str) -> str:
        parts = urlsplit(endpoint)
        if self.key == "yahoo":
            return unquote(parts.path.rsplit("/", 1)[-1])
        return parse_qs(parts.query)["id"][0]

    def get(self, endpoint: str) -> object:
        self.calls.append(endpoint)
        hit = self.routes[self._key(endpoint)]
        if isinstance(hit, Exception):
            raise hit
        return hit


# ---------------------------------------------------------------- Yahoo helpers


def _chart(points: list[tuple[str, float]]) -> dict:
    """A Yahoo chart payload with each point at 14:30 UTC (09:30 New York) of its date."""
    ts = [calendar.timegm(date.fromisoformat(d).timetuple()) + 52200 for d, _ in points]
    adj = [v for _, v in points]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"gmtoffset": NY},
                    "timestamp": ts,
                    "indicators": {"quote": [{"close": [v * 2 for v in adj]}], "adjclose": [{"adjclose": adj}]},
                }
            ],
            "error": None,
        }
    }


ERROR_CHART = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
GOOD_SOX = [("1994-05-04", 100.0), ("1994-05-05", 101.5), ("1994-05-06", 99.25)]
GOOD_NVDA = [("1999-01-22", 0.41), ("1999-01-25", 0.45), ("1999-01-26", 0.42), ("1999-01-27", 0.4)]
THIN = [("2000-01-03", 5.0), ("2000-01-04", 5.5)]  # 2 rows < min_rows 3


def _yahoo_config(tickers: list[tuple[str, str]], required: list[str], start: str = "1990-01-01") -> dict:
    return {
        "asset_class": "equity",
        "sources": {
            "yahoo_history": {
                "endpoint_template": YAHOO_TEMPLATE,
                "start_date": start,
                "tickers": [{"ticker": t, "yahoo": y} for t, y in tickers],
                "required": required,
                "min_rows": 3,
                "output_file": "long_history_prices.csv",
            }
        },
    }


def _yahoo_routes() -> dict[str, object]:
    return {
        "^SOX": _Resp(_chart(GOOD_SOX)),
        "NVDA": _Resp(_chart(GOOD_NVDA)),
        "DOWN": HttpClientError("HTTP 503 after retries"),
        "GONE": _Resp(ERROR_CHART),
        "THIN": _Resp(_chart(THIN)),
    }


def _row_set(df: pd.DataFrame) -> set[tuple[str, str, float]]:
    assert {"date", "ticker", "adj_close"} <= set(df.columns)
    return {
        (pd.Timestamp(d).strftime("%Y-%m-%d"), str(t), float(v))
        for d, t, v in zip(df["date"], df["ticker"], df["adj_close"], strict=True)
    }


def _period1(endpoint: str) -> int:
    return int(parse_qs(urlsplit(endpoint).query)["period1"][0])


@pytest.mark.criterion("C2")
def test_c2_yahoo_point_successes_skips_and_configured_ticker():
    # oracle: Requirement 2 by hand: SOX (Yahoo ^SOX) and NVDA succeed; DOWN raises HttpClientError, GONE has
    # chart.error, THIN has 2 rows < min_rows 3 -- all three non-required, so skipped. The ticker column holds
    # the configured ticker "SOX", not "^SOX". Each ticker is requested once with period1 = 1990-01-01 00:00 UTC.
    cfg = _yahoo_config(
        [("SOX", "^SOX"), ("NVDA", "NVDA"), ("DOWN", "DOWN"), ("GONE", "GONE"), ("THIN", "THIN")], ["SOX"]
    )
    client = _FakeClient(_yahoo_routes(), "yahoo")
    out = YahooHistoryDownloader(cfg, {}, client=client).collect()
    expected = {(d, "SOX", v) for d, v in GOOD_SOX} | {(d, "NVDA", v) for d, v in GOOD_NVDA}
    assert _row_set(out) == expected
    assert len(out) == len(expected)
    assert sorted(client.keys_called()) == sorted(["^SOX", "NVDA", "DOWN", "GONE", "THIN"])
    assert [_period1(e) for e in client.calls] == [PERIOD1_1990] * 5


@pytest.mark.criterion("C2")
def test_c2_yahoo_required_failure_raises_naming_ticker():
    # oracle: Requirement 2: a required ticker that fails (HTTP error, error payload, below min_rows) raises
    # MarketDataIngestError naming the configured ticker. Control: the same config with nothing failing
    # required collects.
    for name, symbol in (("DOWN_T", "DOWN"), ("GONE_T", "GONE"), ("THIN_T", "THIN")):
        cfg = _yahoo_config([("SOX", "^SOX"), (name, symbol)], ["SOX", name])
        with pytest.raises(MarketDataIngestError, match=re.escape(name)):
            YahooHistoryDownloader(cfg, {}, client=_FakeClient(_yahoo_routes(), "yahoo")).collect()
        ok_cfg = _yahoo_config([("SOX", "^SOX"), (name, symbol)], ["SOX"])
        ok = YahooHistoryDownloader(ok_cfg, {}, client=_FakeClient(_yahoo_routes(), "yahoo")).collect()
        assert _row_set(ok) == {(d, "SOX", v) for d, v in GOOD_SOX}


_FAIL_KIND = st.sampled_from(["ok", "http", "error", "thin"])


@pytest.mark.criterion("C2")
@given(
    start=st.dates(min_value=date(1980, 1, 1), max_value=date(2020, 12, 31)),
    kinds=st.lists(_FAIL_KIND, min_size=1, max_size=6),
)
def test_c2_yahoo_property_period1_and_survivors(start: date, kinds: list[str]):
    # oracle: Requirement 2 computed in the test: period1 = calendar.timegm(start 00:00 UTC); the result holds
    # exactly the rows of the "ok" tickers (non-required failures skipped), under the configured ticker.
    routes: dict[str, object] = {}
    expected: set[tuple[str, str, float]] = set()
    tickers = []
    for i, kind in enumerate(kinds):
        sym = f"Y{i}.X"
        tickers.append((f"T{i}", sym))
        pts = [((date(2001, 1, 1) + timedelta(days=k)).isoformat(), float(i * 10 + k + 1)) for k in range(3)]
        if kind == "ok":
            routes[sym] = _Resp(_chart(pts))
            expected |= {(d, f"T{i}", v) for d, v in pts}
        elif kind == "http":
            routes[sym] = HttpClientError("boom")
        elif kind == "error":
            routes[sym] = _Resp(ERROR_CHART)
        else:
            routes[sym] = _Resp(_chart(pts[:2]))
    cfg = _yahoo_config(tickers, [], start=start.isoformat())
    client = _FakeClient(routes, "yahoo")
    out = YahooHistoryDownloader(cfg, {}, client=client).collect()
    assert len(client.calls) == len(kinds)
    assert all(_period1(e) == calendar.timegm(start.timetuple()) for e in client.calls)
    if expected:
        assert _row_set(out) == expected
        assert len(out) == len(expected)
    else:
        assert len(out) == 0


# ---------------------------------------------------------------- FRED

DGS10_CSV = (
    "observation_date,DGS10\n"
    "1989-12-29,7.94\n"
    "1990-01-01,.\n"
    "1990-01-02,7.94\n"
    "1990-01-03,7.99\n"
    "1990-01-04,\n"
    "1990-01-05,7.98\n"
)
VIX_CSV = (
    "DATE,VIXCLS\n"
    "1989-12-29,20.0\n"
    "1990-01-01,.\n"
    "1990-01-02,17.24\n"
    "1990-01-03,18.19\n"
    "1990-01-04,.\n"
    "1990-01-05,18.5\n"
)
THIN_CSV = "observation_date,DEXJPUS\n1990-01-02,146.25\n1990-01-03,.\n1990-01-04,145.5\n"  # 2 rows < 3


def _fred_config(series: list[str], required: list[str], start: str = "1990-01-02") -> dict:
    return {
        "asset_class": "stress",
        "sources": {
            "fred_long": {
                "endpoint_template": FRED_TEMPLATE,
                "series": series,
                "required": required,
                "start_date": start,
                "min_rows": 3,
                "output_file": "fred_long.csv",
            }
        },
    }


def _fred_routes() -> dict[str, object]:
    return {
        "DGS10": _Resp(text=DGS10_CSV),
        "VIXCLS": _Resp(text=VIX_CSV),
        "BAA10Y": HttpClientError("HTTP 500 after retries"),
        "DEXJPUS": _Resp(text=THIN_CSV),
    }


def _wide(df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Column -> {date: value} over non-missing cells, dates normalised to ``YYYY-MM-DD``."""
    dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in df["date"]]
    return {
        c: {d: float(v) for d, v in zip(dates, df[c], strict=True) if pd.notna(v)} for c in df.columns if c != "date"
    }


def _fred_dates(df: pd.DataFrame) -> list[str]:
    return [pd.Timestamp(d).strftime("%Y-%m-%d") for d in df["date"]]


@pytest.mark.criterion("C2")
def test_c2_parse_fredgraph_drops_missing_and_blank():
    # oracle: Requirement 3 by hand: the header line is skipped, "." (1990-01-01) and the blank value
    # (1990-01-04) are dropped; the other four rows are floats keyed by YYYY-MM-DD.
    s = parse_fredgraph(DGS10_CSV, "DGS10")
    got = {pd.Timestamp(k).strftime("%Y-%m-%d"): float(v) for k, v in s.items()}
    assert got == {"1989-12-29": 7.94, "1990-01-02": 7.94, "1990-01-03": 7.99, "1990-01-05": 7.98}


@pytest.mark.criterion("C2")
def test_c2_fred_point_wide_frame_restricted_and_skips():
    # oracle: Requirement 3 by hand: start_date 1990-01-02 removes 1989-12-29; "."/blank rows dropped; BAA10Y
    # (HttpClientError) and DEXJPUS (2 rows < min_rows 3) are non-required, so skipped. Columns: date, DGS10,
    # VIXCLS; dates 1990-01-02, 01-03, 01-05 ascending. Each series requested once.
    client = _FakeClient(_fred_routes(), "fred")
    cfg = _fred_config(["DGS10", "VIXCLS", "BAA10Y", "DEXJPUS"], ["DGS10"])
    out = FredLongDownloader(cfg, {}, client=client).collect()
    assert set(out.columns) == {"date", "DGS10", "VIXCLS"}
    assert _fred_dates(out) == ["1990-01-02", "1990-01-03", "1990-01-05"]
    assert _wide(out) == {
        "DGS10": {"1990-01-02": 7.94, "1990-01-03": 7.99, "1990-01-05": 7.98},
        "VIXCLS": {"1990-01-02": 17.24, "1990-01-03": 18.19, "1990-01-05": 18.5},
    }
    assert sorted(client.keys_called()) == ["BAA10Y", "DEXJPUS", "DGS10", "VIXCLS"]


@pytest.mark.criterion("C2")
def test_c2_fred_required_failure_raises_naming_series():
    # oracle: Requirement 3 (failure handling as Requirement 2): required BAA10Y (HTTP error) or DEXJPUS
    # (below min_rows) raises MarketDataIngestError naming it. Control: DGS10 alone, required, collects.
    for bad in ("BAA10Y", "DEXJPUS"):
        cfg = _fred_config(["DGS10", bad], ["DGS10", bad])
        with pytest.raises(MarketDataIngestError, match=re.escape(bad)):
            FredLongDownloader(cfg, {}, client=_FakeClient(_fred_routes(), "fred")).collect()
    ok_client = _FakeClient(_fred_routes(), "fred")
    ok = FredLongDownloader(_fred_config(["DGS10"], ["DGS10"]), {}, client=ok_client).collect()
    assert _wide(ok) == {"DGS10": {"1990-01-02": 7.94, "1990-01-03": 7.99, "1990-01-05": 7.98}}


_CELL = st.one_of(
    st.integers(min_value=1, max_value=10_000).map(lambda i: i / 100),
    st.just("."),
    st.just(""),
)


@pytest.mark.criterion("C2")
@given(cells=st.lists(_CELL, min_size=1, max_size=30), cut=st.integers(min_value=0, max_value=30))
def test_c2_fred_property_start_date_and_missing(cells: list[object], cut: int):
    # oracle: Requirement 3 computed in the test: rows with "." or blank dropped, then dates < start_date dropped;
    # parse_fredgraph keeps every valid row. min_rows 0 so the floor never bites here.
    days = [(date(1995, 6, 1) + timedelta(days=k)).isoformat() for k in range(len(cells))]
    text = "observation_date,SER\n" + "".join(f"{d},{'' if c == '' else c}\n" for d, c in zip(days, cells, strict=True))
    valid = {d: float(c) for d, c in zip(days, cells, strict=True) if c not in (".", "")}
    start = (date(1995, 6, 1) + timedelta(days=cut)).isoformat()
    kept = {d: v for d, v in valid.items() if d >= start}
    parsed = parse_fredgraph(text, "SER")
    assert {pd.Timestamp(k).strftime("%Y-%m-%d"): float(v) for k, v in parsed.items()} == valid
    if not kept:
        return
    cfg = _fred_config(["SER"], [], start=start)
    cfg["sources"]["fred_long"]["min_rows"] = 0
    out = FredLongDownloader(cfg, {}, client=_FakeClient({"SER": _Resp(text=text)}, "fred")).collect()
    assert _fred_dates(out) == sorted(kept)
    assert _wide(out) == {"SER": kept}
