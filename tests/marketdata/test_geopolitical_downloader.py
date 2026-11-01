"""Offline tests for the F8 GPR + EPU downloaders (market-intelligence P7).

A fake client returns in-memory workbooks/CSV, so nothing hits the network. Verifies trap 2 (the
binary .xls is read as bytes -> BytesIO -> read_excel), the daily-global + monthly-country split,
the fail-loud floor, and the EPU year/month/day -> date assembly.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest
from src.marketdata.common.errors import MarketDataIngestError
from src.marketdata.stress.geopolitical_downloader import EPUDownloader, GPRDownloader


class _Resp:
    def __init__(self, *, content: bytes = b"", text: str = ""):
        self.content = content
        self.text = text


class _FakeClient:
    def __init__(self, by_key: dict[str, _Resp]):
        self._by = by_key

    def get(self, endpoint: str) -> _Resp:
        for key, resp in self._by.items():
            if key in endpoint:
                return resp
        raise RuntimeError(f"no canned response for {endpoint}")


def _xls(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="Sheet1", index=False)
    return buf.getvalue()


def _cfg() -> dict:
    return {
        "asset_class": "stress",
        "sources": {
            "gpr": {"daily_endpoint": "/daily.xls", "country_endpoint": "/country.xls",
                    "country_columns": ["GPRC_TWN", "GPRC_CHN"], "min_expected_rows": 3,
                    "output_files": {"daily": "gpr_daily.csv", "country": "gpr_country.csv"},
                    "base_url": "https://x", "auth": {"type": "none"}},
            "epu": {"endpoint": "/epu.csv", "output_file": "epu_daily.csv",
                    "base_url": "https://x", "auth": {"type": "none"}},
        },
    }


def _patch(monkeypatch, written: dict) -> None:
    monkeypatch.setattr("src.marketdata.stress.geopolitical_downloader.get_output_path",
                        lambda ac, f: f)
    monkeypatch.setattr("src.marketdata.stress.geopolitical_downloader.save_csv",
                        lambda df, path: written.__setitem__(path, df))


def test_gpr_parses_daily_and_country_via_bytesio(monkeypatch) -> None:
    written: dict = {}
    _patch(monkeypatch, written)
    daily = pd.DataFrame({
        "DAY": [20260101, 20260102, 20260103, 20260104],
        "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
        "GPRD": [100.0, 110.0, 120.0, 130.0],
        "GPRD_ACT": [90.0, 95.0, 100.0, 105.0],
        "GPRD_THREAT": [110.0, 120.0, 130.0, 140.0],
    })
    country = pd.DataFrame({
        "month": pd.to_datetime(["2026-04-01", "2026-05-01", "2026-06-01"]),
        "GPRC_TWN": [0.25, 0.53, 0.18], "GPRC_CHN": [1.7, 1.7, 0.8], "OTHER": [1, 2, 3],
    })
    client = _FakeClient({"/daily.xls": _Resp(content=_xls(daily)),
                          "/country.xls": _Resp(content=_xls(country))})
    GPRDownloader(_cfg(), {}, client=client).run()

    d = written["gpr_daily.csv"]
    assert list(d.columns) == ["date", "GPRD", "GPRD_ACT", "GPRD_THREAT"]
    assert d["GPRD"].tolist() == [100.0, 110.0, 120.0, 130.0]
    c = written["gpr_country.csv"]
    assert list(c.columns) == ["month", "GPRC_TWN", "GPRC_CHN"]      # OTHER dropped (not requested)
    assert c["GPRC_TWN"].tolist() == [0.25, 0.53, 0.18]


def test_gpr_floor_fails_loud(monkeypatch) -> None:
    _patch(monkeypatch, {})
    daily = pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "GPRD": [100.0],
                          "GPRD_ACT": [90.0], "GPRD_THREAT": [110.0]})     # 1 row < floor 3
    country = pd.DataFrame({"month": pd.to_datetime(["2026-06-01"]), "GPRC_TWN": [0.18],
                            "GPRC_CHN": [0.8]})
    client = _FakeClient({"/daily.xls": _Resp(content=_xls(daily)),
                          "/country.xls": _Resp(content=_xls(country))})
    with pytest.raises(MarketDataIngestError, match="floor"):
        GPRDownloader(_cfg(), {}, client=client).run()


def test_epu_assembles_date_from_ymd(monkeypatch) -> None:
    written: dict = {}
    _patch(monkeypatch, written)
    csv = "year,month,day,daily_policy_index\n2026,1,2,103.8\n2026,1,3,88.2\n"
    EPUDownloader(_cfg(), {}, client=_FakeClient({"/epu.csv": _Resp(text=csv)})).run()
    out = written["epu_daily.csv"]
    assert list(out.columns) == ["date", "epu"]
    assert out["date"].tolist() == ["2026-01-02", "2026-01-03"]
    assert out["epu"].tolist() == [103.8, 88.2]
