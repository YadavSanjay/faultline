"""Offline tests for the news ingest (A0 CIK map + A1 8-K events, market-intelligence P3).

A fake HTTP client returns canned SEC responses, so nothing hits the network. Covers the
coverage floor, the drift check vs the in-repo bond-issuer CIKs, the 8-K item-code allowlist +
weighting, and the fail-loud event floor.
"""

from __future__ import annotations

import pytest
from src.marketdata.common.errors import MarketDataIngestError
from src.marketdata.news.cik_map import CikMapDownloader
from src.marketdata.news.sec_events_downloader import SecEventsDownloader


class _Resp:
    def __init__(self, payload: object):
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeClient:
    def __init__(self, by_endpoint: dict[str, object]):
        self._by = by_endpoint
        self.calls: list[str] = []

    def get(self, endpoint: str) -> _Resp:
        self.calls.append(endpoint)
        for key, payload in self._by.items():
            if key in endpoint:
                return _Resp(payload)
        raise RuntimeError(f"no canned response for {endpoint}")


# real SEC CIKs so the drift check against configs/marketdata/bonds passes
_TICKERS = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
            "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "2": {"cik_str": 1341439, "ticker": "ORCL", "title": "ORACLE CORP"}}


def _cfg() -> dict:
    return {
        "asset_class": "news",
        "tickers": ["NVDA", "AAPL", "ORCL"],
        "cik_coverage_floor": 2,
        "cik_map_file": "cik_map.csv",
        "time_window": {"start_date": "2024-01-01", "end_date": "2026-12-31"},
        "item_allowlist": {"5.02": 1.0, "2.02": 0.6, "8.01": 0.2},
        "sources": {
            "sec_company_tickers": {"endpoint": "/files/company_tickers.json",
                                    "output_file": "cik_map.csv", "base_url": "https://x",
                                    "auth": {"type": "none"}},
            "sec_events": {"endpoint_template": "/submissions/CIK{cik}.json",
                           "output_file": "sec_events.csv", "min_expected_events": 1,
                           "base_url": "https://x", "auth": {"type": "none"}},
        },
    }


def _patch_io(monkeypatch, module: str, written: dict) -> None:
    monkeypatch.setattr(f"src.marketdata.news.{module}.get_output_path",
                        lambda ac, f: f"__mem__/{f}")
    monkeypatch.setattr(f"src.marketdata.news.{module}.save_csv",
                        lambda df, path: written.__setitem__(path, df))


def test_cik_map_resolves_and_drift_check_passes(monkeypatch) -> None:
    written: dict = {}
    _patch_io(monkeypatch, "cik_map", written)
    dl = CikMapDownloader(_cfg(), {}, client=_FakeClient({"company_tickers.json": _TICKERS}))
    df = dl.run()
    assert set(df["ticker"]) == {"NVDA", "AAPL", "ORCL"}
    assert df.set_index("ticker").loc["AAPL", "cik"] == "0000320193"   # zero-padded to 10


def test_cik_map_drift_vs_bonds_config_fails_loud(monkeypatch) -> None:
    _patch_io(monkeypatch, "cik_map", {})
    bad = dict(_TICKERS, **{"1": {"cik_str": 999999, "ticker": "AAPL", "title": "Apple Inc."}})
    dl = CikMapDownloader(_cfg(), {}, client=_FakeClient({"company_tickers.json": bad}))
    with pytest.raises(MarketDataIngestError, match="drift"):
        dl.run()


def test_cik_map_coverage_floor_fails_loud(monkeypatch) -> None:
    _patch_io(monkeypatch, "cik_map", {})
    cfg = dict(_cfg(), tickers=["NVDA", "AAPL", "ORCL", "ZZZZ", "YYYY"], cik_coverage_floor=4)
    dl = CikMapDownloader(cfg, {}, client=_FakeClient({"company_tickers.json": _TICKERS}))
    with pytest.raises(MarketDataIngestError, match="floor"):
        dl.run()


def _submissions(forms, items, dates, accs):
    return {"filings": {"recent": {
        "form": forms, "items": items, "filingDate": dates, "accessionNumber": accs,
        "primaryDocument": ["d.htm"] * len(forms)}}}


def test_sec_events_filters_allowlist_and_weights(monkeypatch) -> None:
    written: dict = {}
    _patch_io(monkeypatch, "sec_events_downloader", written)
    # CIK map read: monkeypatch load_csv to return the resolved map
    import pandas as pd
    cik_df = pd.DataFrame({"ticker": ["NVDA"], "cik": ["0001045810"]})
    monkeypatch.setattr("src.marketdata.news.sec_events_downloader.load_csv", lambda p: cik_df)

    subs = _submissions(
        forms=["8-K", "8-K", "10-Q", "8-K"],
        items=["5.02,9.01", "7.99", "", "2.02,8.01"],   # row2 no allowlisted item; row3 not 8-K
        dates=["2026-07-02", "2026-06-01", "2026-05-01", "2026-05-20"],
        accs=["0001045810-26-000060", "a-2", "a-3", "0001045810-26-000028"])
    dl = SecEventsDownloader(_cfg(), {}, client=_FakeClient({"CIK0001045810": subs}))
    df = dl.run()

    assert len(df) == 2                                  # only the two allowlisted 8-Ks
    keyman = df[df["item_codes"] == "5.02"].iloc[0]
    assert keyman["weight"] == 1.0                       # 5.02 weight
    earn = df[df["item_codes"] == "2.02,8.01"].iloc[0]
    assert earn["weight"] == pytest.approx(0.8)          # 2.02 (0.6) + 8.01 (0.2)
    assert "000104581026000060" in keyman["doc_url"]


def test_sec_events_floor_fails_loud_on_empty(monkeypatch) -> None:
    _patch_io(monkeypatch, "sec_events_downloader", {})
    import pandas as pd
    monkeypatch.setattr("src.marketdata.news.sec_events_downloader.load_csv",
                        lambda p: pd.DataFrame({"ticker": ["NVDA"], "cik": ["0001045810"]}))
    subs = _submissions(["10-K"], [""], ["2026-01-01"], ["a-1"])   # no 8-K at all
    dl = SecEventsDownloader(_cfg(), {}, client=_FakeClient({"CIK0001045810": subs}))
    with pytest.raises(MarketDataIngestError):
        dl.run()


_TSM_RULES = [{"pattern": "revenue|monthend|dividend|fsx|agm", "item": None},
              {"pattern": "changeof", "item": "5.02"},
              {"pattern": r"^tsm-\d{8}x6k\.htm$", "item": "2.02"},
              {"pattern": "board|directorsresolut", "item": "8.01"}]


def test_sec_events_adds_rule_mapped_6ks_from_the_same_submissions(monkeypatch) -> None:
    """sec-6k-events wiring: a 6-K filer's mapped 6-Ks join its 8-K rows; routine 6-Ks are dropped."""
    import pandas as pd
    written: dict = {}
    _patch_io(monkeypatch, "sec_events_downloader", written)
    monkeypatch.setattr("src.marketdata.news.sec_events_downloader.load_csv",
                        lambda p: pd.DataFrame({"ticker": ["TSM", "NVDA"], "cik": ["0001046179", "0001045810"]}))
    tsm = {"filings": {"recent": {
        "form": ["6-K", "6-K", "6-K", "6-K", "20-F"],
        "items": ["", "", "", "", ""],
        "filingDate": ["2026-10-15", "2026-10-08", "2025-11-05", "2026-08-11", "2026-04-01"],
        "accessionNumber": ["0001046179-26-000140", "0001046179-26-000139", "0001046179-25-000120",
                            "0001046179-26-000100", "0001046179-26-000050"],
        "primaryDocument": ["tsm-20261015x6k.htm", "tsm-revenue20261008.htm", "a20251105changeofnaceo.htm",
                            "tsm-boardx20260811.htm", "tsm-20f.htm"]}}}
    nvda = _submissions(["8-K"], ["5.02"], ["2026-07-02"], ["0001045810-26-000060"])
    cfg = {**_cfg(), "six_k_rules": _TSM_RULES}
    df = SecEventsDownloader(cfg, {}, client=_FakeClient({"CIK0001046179": tsm, "CIK0001045810": nvda})).run()
    by = df.set_index("accession")
    assert set(df["ticker"]) == {"TSM", "NVDA"} and len(df) == 4         # 3 mapped 6-Ks + the 8-K
    assert by.loc["0001046179-26-000140", "item_codes"] == "2.02"         # earnings release
    assert by.loc["0001046179-25-000120", "item_codes"] == "5.02"         # officer change
    assert by.loc["0001046179-26-000100", "weight"] == pytest.approx(0.2)  # board resolution -> 8.01
    assert "0001046179-26-000139" not in by.index                          # monthly revenue: routine
    assert set(df[df["ticker"] == "TSM"]["form"]) == {"6-K"}
    assert by.loc["0001045810-26-000060", "form"] == "8-K"
    assert "/1046179/000104617926000140/" in by.loc["0001046179-26-000140", "doc_url"]


def test_sec_events_without_six_k_rules_keeps_the_8k_output(monkeypatch) -> None:
    """No rules configured: 6-Ks are ignored and the 8-K rows are exactly as before."""
    import pandas as pd
    _patch_io(monkeypatch, "sec_events_downloader", {})
    monkeypatch.setattr("src.marketdata.news.sec_events_downloader.load_csv",
                        lambda p: pd.DataFrame({"ticker": ["NVDA"], "cik": ["0001045810"]}))
    subs = _submissions(["8-K", "6-K"], ["2.02", ""], ["2026-07-02", "2026-07-03"], ["a-1", "a-2"])
    df = SecEventsDownloader(_cfg(), {}, client=_FakeClient({"CIK0001045810": subs})).run()
    assert list(df["accession"]) == ["a-1"] and list(df["form"]) == ["8-K"]


def test_sec_events_mistyped_six_k_rule_fails_at_startup() -> None:
    cfg = {**_cfg(), "six_k_rules": [{"pattern": "board", "item": "8.1"}]}       # not an allowlisted code
    with pytest.raises(ValueError, match="allowlist"):
        SecEventsDownloader(cfg, {}, client=_FakeClient({}))
