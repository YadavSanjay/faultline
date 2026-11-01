"""Offline test for the Ken French style-factor parser (F4).

Exercises the multi-line-header / trailing-footer parsing on a synthetic French-format CSV, so no
network or ZIP is needed. The download path (ZIP -> BytesIO) is covered by a fake HTTP client.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
from src.marketdata.factors.french_factors_downloader import (
    FrenchFactorsDownloader,
    _parse_french_csv,
)

_MOM_CSV = """This file was created ... (prose header)
another header line

,Mom,
19261103, 0.35,
19261104,-0.61,
20260529,-1.68,

Copyright 2026 Eugene F. Fama and Kenneth R. French,,
"""


def test_parse_skips_header_and_footer():
    df = _parse_french_csv(_MOM_CSV, ["mom"])
    assert list(df.columns) == ["date", "mom"]
    assert len(df) == 3                                  # 3 data rows, prose + copyright dropped
    assert df["mom"].iloc[0] == 0.35
    assert str(df["date"].iloc[-1].date()) == "2026-05-29"


def test_parse_stops_at_a_second_block():
    # a trailing annual block (4-digit "dates") must not be mixed into the daily rows
    text = ",Mom,\n20260101,1.0,\n20260102,2.0,\n\n Annual Factors:\n2025,10.0,\n"
    df = _parse_french_csv(text, ["mom"])
    assert len(df) == 2
    assert set(df["mom"]) == {1.0, 2.0}


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content


class _FakeClient:
    """Serves a zipped French CSV per endpoint, matching HttpClient.get(...).content."""

    def __init__(self, by_endpoint: dict[str, bytes]):
        self._by = by_endpoint

    def get(self, endpoint: str) -> _FakeResp:
        return _FakeResp(self._by[endpoint])


def _zip(csv_text: str, name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, csv_text)
    return buf.getvalue()


def test_downloader_merges_two_archives_on_date():
    ff5 = (",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
           "20260528,0.67,-0.16,-0.59,-1.08,0.05,0.02\n"
           "20260529,0.19,-1.03,-0.26,-0.66,0.15,0.02\n")
    mom = ",Mom,\n20260528,-0.91,\n20260529,-1.68,\n"
    cfg = {
        "asset_class": "factors",
        "sources": {"ken_french": {
            "archives": {
                "ff5": {"endpoint": "/ff5", "columns": ["mkt_rf", "smb", "hml", "rmw", "cma", "rf"]},
                "momentum": {"endpoint": "/mom", "columns": ["mom"]},
            },
            "output_dir": "", "output_file": "ff_factors.csv",
        }},
    }
    client = _FakeClient({"/ff5": _zip(ff5, "F-F_Research_Data_5_Factors_2x3_daily.csv"),
                          "/mom": _zip(mom, "F-F_Momentum_Factor_daily.csv")})
    dl = FrenchFactorsDownloader(cfg, {}, client=client)
    # exercise the parse+merge without the save_csv side effect
    merged = None
    for key, spec in cfg["sources"]["ken_french"]["archives"].items():
        df = dl._fetch_archive(key, spec["endpoint"], spec["columns"])
        merged = df if merged is None else merged.merge(df, on="date", how="outer")
    assert merged is not None
    assert set(merged.columns) == {"date", "mkt_rf", "smb", "hml", "rmw", "cma", "rf", "mom"}
    assert len(merged) == 2
    row = merged[merged["date"] == pd.Timestamp("2026-05-29")].iloc[0]
    assert row["mom"] == -1.68 and row["mkt_rf"] == 0.19
