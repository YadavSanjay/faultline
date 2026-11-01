"""Offline test for the NY Fed ACM term-premium downloader (F6, market-intelligence P5).

Verifies trap 2: the binary .xls is read as bytes and parsed via BytesIO -> read_excel (never a
raw-path read, which would break under the dual-mode s3:// facade). A fake client returns an
in-memory workbook, so nothing hits the network.
"""

from __future__ import annotations

import io

import pandas as pd
from src.marketdata.rates.nyfed_acm_downloader import NYFedACMDownloader


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content


class _FakeClient:
    def __init__(self, content: bytes):
        self._content = content
        self.requested: list[str] = []

    def get(self, endpoint: str) -> _FakeResp:
        self.requested.append(endpoint)
        return _FakeResp(self._content)


def _workbook_bytes() -> bytes:
    # mimic the ACM "ACM Daily" sheet: DATE + fitted yields + ACMTP01..10
    df = pd.DataFrame({
        "DATE": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "ACMY10": [4.0, 4.1, 4.2],
        "ACMTP02": [0.10, 0.11, 0.12],
        "ACMTP10": [0.50, 0.55, 0.60],
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="ACM Daily", index=False)
        df.to_excel(xw, sheet_name="ACM Monthly", index=False)
    return buf.getvalue()


def _config() -> dict:
    return {
        "asset_class": "rates",
        "time_window": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
        "sources": {"nyfed_acm": {
            "endpoint": "/acm.xls", "sheet": "ACM Daily", "columns": ["ACMTP10"],
            "output_file": "nyfed_acm_term_premium.csv", "base_url": "https://x", "auth": {"type": "none"},
        }},
    }


def test_parses_the_acm_daily_sheet_via_bytesio(tmp_path, monkeypatch):
    written: dict[str, pd.DataFrame] = {}
    monkeypatch.setattr("src.marketdata.rates.nyfed_acm_downloader.get_output_path",
                        lambda ac, f: str(tmp_path / f))
    monkeypatch.setattr("src.marketdata.rates.nyfed_acm_downloader.upsert_csv",
                        lambda df, path, date_col="date": written.setdefault("df", df))

    client = _FakeClient(_workbook_bytes())
    dl = NYFedACMDownloader(_config(), {}, client=client)
    out = dl.run()

    assert client.requested == ["/acm.xls"]
    assert list(out.columns) == ["date", "ACMTP10"]      # only the requested column is kept
    assert len(out) == 3
    assert out["ACMTP10"].tolist() == [0.50, 0.55, 0.60]
    assert written["df"]["ACMTP10"].tolist() == [0.50, 0.55, 0.60]


def test_time_window_filter_applies(tmp_path, monkeypatch):
    monkeypatch.setattr("src.marketdata.rates.nyfed_acm_downloader.get_output_path",
                        lambda ac, f: str(tmp_path / f))
    monkeypatch.setattr("src.marketdata.rates.nyfed_acm_downloader.upsert_csv",
                        lambda df, path, date_col="date": None)
    cfg = _config()
    cfg["time_window"] = {"start_date": "2024-01-03", "end_date": "2024-12-31"}
    dl = NYFedACMDownloader(cfg, {}, client=_FakeClient(_workbook_bytes()))
    out = dl.run()
    assert out["date"].tolist() == ["2024-01-03", "2024-01-04"]     # 2024-01-02 filtered out
