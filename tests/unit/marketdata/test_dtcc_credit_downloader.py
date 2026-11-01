"""Offline tests for the DTCC credit downloader wiring (credit-sdr-dedup).

The locked spec tests cover the record functions; these cover the downloader around them: every
same-day record kept with exact identifiers and spread strings, corrections and cancellations
applied across runs (including a day fetched late), the legacy one-row-per-day file replaced, and
nothing written when a run fetches nothing. A fake client returns in-memory zips.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest
from src.marketdata.credit.dtcc_credit_downloader import DTCCIndexDownloader, DTCCSingleNameDownloader
from src.marketdata.rates.sdr_records import read_sdr_records

DID = "Dissemination Identifier"
ODID = "Original Dissemination Identifier"


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content


class _FakeClient:
    """Serves one zip per business date; dates without a zip raise like a 404."""

    def __init__(self, zips: dict[str, bytes]):
        self._zips = zips
        self.requested: list[str] = []

    def get(self, endpoint: str) -> _FakeResp:
        day = endpoint.rsplit("CREDITS_", 1)[1].removesuffix(".zip").replace("_", "-")
        self.requested.append(day)
        if day not in self._zips:
            raise RuntimeError(f"404 {endpoint}")
        return _FakeResp(self._zips[day])


def _raw(did: str, *, name: str = "ORACLE CORPORATION", action: str = "NEWT", event: str = "TRAD",
         orig: str = "", spread: str = "0.007125", ts: str = "2026-09-28T14:00:00Z") -> dict[str, str]:
    return {
        DID: did, ODID: orig, "Action type": action, "Event type": event, "Event timestamp": ts,
        "Execution Timestamp": ts, "Expiration Date": "2031-12-20", "Underlying Asset Name": name,
        "UPI Underlier Name": "", "Spread-Leg 1": spread, "Fixed rate-Leg 1": "0.01",
        "Notional amount-Leg 1": "5,000,000+", "Custom basket indicator": "FALSE",
    }


def _zip(rows: list[dict[str, str]], day: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"SEC_CUMULATIVE_CREDITS_{day.replace('-', '_')}.csv", pd.DataFrame(rows).to_csv(index=False))
    return buf.getvalue()


def _config(end: str = "2026-09-30", lookback: int | None = None) -> dict:
    src = {
        "base_url": "https://x", "auth": {"type": "none"},
        "file_pattern": "/sec/eod/SEC_CUMULATIVE_CREDITS_{date}.zip",
        "filter_column": "Underlying Asset Name",
        "filter_keywords": ["ORACLE", "META PLATFORM"],
        "exclude_keywords": ["BIDCO"],
        "output_file": "dtcc_single_name_cds.csv",
    }
    if lookback is not None:
        src["lookback_business_days"] = lookback
    return {"asset_class": "credit", "dtcc_start_date": "2026-09-28",
            "time_window": {"start_date": "2022-01-01", "end_date": end},
            "sources": {"dtcc_single_name": src, "dtcc_index": src}}


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("src.marketdata.credit.dtcc_credit_downloader.get_output_path",
                        lambda ac, f: tmp_path / f)
    monkeypatch.setattr("src.marketdata.credit.dtcc_credit_downloader.write_provenance",
                        lambda path, df, source_url="": None)
    return tmp_path


def _downloader(config: dict, zips: dict[str, bytes]) -> tuple[DTCCSingleNameDownloader, _FakeClient]:
    dl = DTCCSingleNameDownloader(config, {})
    client = _FakeClient(zips)
    dl._client = client  # the network boundary
    return dl, client


A, B, C, D = "5613678104000000201", "5614615265000000101", "5612067808000000101", "5614409086000000101"
B_EROR, C_CORR = "5615315077000000999", "5615315077000000555"


def test_keeps_every_same_day_record_with_exact_values(out_dir):
    day = [_raw(A), _raw(B, name="META PLATFORMS INC."), _raw(C, action="MODI"),
           _raw(D, name="ORACLE BIDCO LLC"),                      # excluded keyword
           _raw("5600000000000000001", name="APPLE INC.")]        # not a configured name
    dl, _ = _downloader(_config(end="2026-09-28"), {"2026-09-28": _zip(day, "2026-09-28")})
    dl.run()

    out = read_sdr_records(out_dir / "dtcc_single_name_cds.csv")
    assert sorted(out[DID]) == sorted([A, B, C])                   # MODI kept too: every action type
    assert set(out["Spread-Leg 1"]) == {"0.007125"}                 # the exact string, not a float
    assert "Custom basket indicator" not in out.columns             # credit output columns only
    assert "Fixed rate payment frequency period-Leg 1" not in out.columns   # no rates columns added


def test_corrections_and_late_cancellations_apply_across_runs(out_dir):
    d1 = _zip([_raw(A), _raw(B), _raw(C, spread="0.007000")], "2026-09-28")
    d2 = _zip([_raw(B_EROR, action="EROR", event="", orig=B, ts="2026-09-29T10:00:00Z"),
               _raw(C_CORR, action="CORR", event="", orig=C, spread="0.007250", ts="2026-09-29T11:00:00Z")],
              "2026-09-29")
    dl, _ = _downloader(_config(end="2026-09-29"), {"2026-09-29": d2})      # 09-28 fails this run
    dl.run()
    dl, client = _downloader(_config(end="2026-09-29"), {"2026-09-28": d1, "2026-09-29": d2})
    dl.run()

    assert client.requested == ["2026-09-28"]
    out = read_sdr_records(out_dir / "dtcc_single_name_cds.csv")
    assert sorted(out[DID]) == sorted([A, C_CORR])                  # B cancelled late; C corrected
    assert list(out.loc[out[DID] == C_CORR, "Spread-Leg 1"]) == ["0.007250"]


def test_legacy_file_is_replaced_and_failed_run_writes_nothing(out_dir):
    legacy = pd.DataFrame({"date": ["2026-09-28"], "Underlying Asset Name": ["ORACLE CORPORATION"],
                           "Spread-Leg 1": [0.0071], "Action type": ["NEWT"]})
    path = out_dir / "dtcc_single_name_cds.csv"
    legacy.to_csv(path, index=False)
    before = path.read_bytes()

    dl, client = _downloader(_config(), {})                          # every fetch fails
    dl.run()
    assert client.requested == ["2026-09-28", "2026-09-29", "2026-09-30"]   # legacy -> full window
    assert path.read_bytes() == before

    dl, _ = _downloader(_config(end="2026-09-28"), {"2026-09-28": _zip([_raw(A)], "2026-09-28")})
    dl.run()
    out = read_sdr_records(path)
    assert DID in out.columns and list(out[DID]) == [A]


def test_index_downloader_filters_on_upi_underlier(out_dir):
    cfg = _config(end="2026-09-28")
    cfg["sources"]["dtcc_index"] = dict(cfg["sources"]["dtcc_index"], filter_column="UPI Underlier Name",
                                        filter_keywords=["CDX.NA.IG"], exclude_keywords=[],
                                        output_file="dtcc_index_cds.csv")
    rows = [dict(_raw(A, name=""), **{"UPI Underlier Name": "CDX.NA.IG"}),
            dict(_raw(B, name=""), **{"UPI Underlier Name": "CDX.NA.HY"})]
    dl = DTCCIndexDownloader(cfg, {})
    dl._client = _FakeClient({"2026-09-28": _zip(rows, "2026-09-28")})
    dl.run()

    out = read_sdr_records(out_dir / "dtcc_index_cds.csv")
    assert list(out[DID]) == [A]
