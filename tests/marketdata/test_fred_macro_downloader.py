"""Offline tests for the FRED stress downloaders (credit-OAS macro pull + F9 throughput).

A fake client returns canned fredgraph CSVs, so nothing hits the network. Guards the regression
where the F9 subclass split the base class and left ``FREDMacroDownloader`` without ``run()``,
so the ``fred_macro`` source (the VaR CREDIT_IG/HY factor file) silently stopped refreshing.
"""

from __future__ import annotations

import pandas as pd
from src.marketdata.stress.fred_macro_downloader import FREDMacroDownloader, FREDThroughputDownloader


class _Resp:
    def __init__(self, text: str):
        self.text = text


class _FakeClient:
    def __init__(self, by_series: dict[str, str]):
        self._by = by_series

    def get(self, endpoint: str) -> _Resp:
        for series_id, text in self._by.items():
            if f"id={series_id}" in endpoint:
                return _Resp(text)
        raise RuntimeError(f"no canned response for {endpoint}")


def _cfg() -> dict:
    tpl = "/graph/fredgraph.csv?id={series_id}"
    return {
        "asset_class": "stress",
        "time_window": {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        "sources": {
            "fred_macro": {"endpoint_template": tpl, "output_file": "fred_stress_macro.csv",
                           "series": {"BAMLC0A0CM": "IG OAS", "VIXCLS": "VIX"},
                           "base_url": "https://x", "auth": {"type": "none"}},
            "fred_throughput": {"endpoint_template": tpl, "output_file": "fred_throughput.csv",
                                "series": {"IPG3344S": "semi IP"},
                                "base_url": "https://x", "auth": {"type": "none"}},
        },
    }


def _patch(monkeypatch, written: dict) -> None:
    monkeypatch.setattr("src.marketdata.stress.fred_macro_downloader.get_output_path",
                        lambda ac, f: f)
    monkeypatch.setattr("src.marketdata.stress.fred_macro_downloader.upsert_csv",
                        lambda df, path, date_col: written.__setitem__(path, df))


def test_macro_downloader_merges_series_into_its_own_file(monkeypatch) -> None:
    written: dict = {}
    _patch(monkeypatch, written)
    client = _FakeClient({
        "BAMLC0A0CM": "observation_date,BAMLC0A0CM\n2026-10-01,0.80\n2026-10-02,.\n",
        "VIXCLS": "observation_date,VIXCLS\n2026-10-01,16.5\n2026-10-02,17.0\n",
    })

    out = FREDMacroDownloader(_cfg(), {}, client=client).run()

    assert list(written) == ["fred_stress_macro.csv"]
    assert list(out["date"]) == ["2026-10-01", "2026-10-02"]
    assert out["BAMLC0A0CM"].iloc[0] == 0.80
    assert pd.isna(out["BAMLC0A0CM"].iloc[1])            # FRED "." -> NaN
    assert list(out["VIXCLS"]) == [16.5, 17.0]


def test_throughput_downloader_reads_its_own_source_key(monkeypatch) -> None:
    written: dict = {}
    _patch(monkeypatch, written)
    client = _FakeClient({"IPG3344S": "observation_date,IPG3344S\n2026-08-01,151.2\n"})

    out = FREDThroughputDownloader(_cfg(), {}, client=client).run()

    assert list(written) == ["fred_throughput.csv"]
    assert list(out.columns) == ["date", "IPG3344S"]
