"""Offline tests for the DTCC SDR rates downloader wiring (sdr-dedup).

The locked spec tests (tests/spec/sdr-dedup) cover the record functions; these cover the downloader
around them: raw zips parsed as exact strings, every same-day SOFR record kept, corrections applied
across runs, the legacy one-row-per-day file replaced, the rolling window, and no overwrite when a
run fetches nothing. A fake client returns in-memory zips, so nothing hits the network.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest
from src.marketdata.rates.dtcc_sdr_downloader import DTCCSDRRatesDownloader
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
        day = endpoint.rsplit("RATES_", 1)[1].removesuffix(".zip").replace("_", "-")
        self.requested.append(day)
        if day not in self._zips:
            raise RuntimeError(f"404 {endpoint}")
        return _FakeResp(self._zips[day])


def _raw(did: str, *, action: str = "NEWT", orig: str = "", ccy: str = "USD",
         underlier: str = "USD-SOFR-OIS Compound", premium: str = "", rate: str = "0.0375",
         ts: str = "2026-09-28T14:00:00Z") -> dict[str, str]:
    return {
        DID: did, ODID: orig, "Action type": action, "Event type": "TRAD", "Event timestamp": ts,
        "Execution Timestamp": ts, "Effective Date": "2026-09-30", "Expiration Date": "2036-09-30",
        "Notional currency-Leg 1": ccy, "Notional amount-Leg 1": "50,000,000",
        "UPI Underlier Name": underlier, "UPI FISN": "NA/Swap OIS USD",
        "Fixed rate-Leg 1": rate, "Option Premium Amount": premium,
    }


def _zip(rows: list[dict[str, str]], day: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"CFTC_CUMULATIVE_RATES_{day.replace('-', '_')}.csv", pd.DataFrame(rows).to_csv(index=False))
    return buf.getvalue()


def _config(start: str = "2026-09-28", end: str = "2026-09-30", lookback: int | None = None) -> dict:
    src = {
        "base_url": "https://x", "auth": {"type": "none"},
        "file_pattern": "/cftc/eod/CFTC_CUMULATIVE_RATES_{date}.zip",
        "start_date_override": start,
        "filters": {"upi_underlier_keywords": ["USD-SOFR", "SOFR-OIS"], "currency": "USD"},
        "output_files": {"irs": "dtcc_sdr_irs.csv", "options": "dtcc_sdr_ir_options.csv"},
    }
    if lookback is not None:
        src["lookback_business_days"] = lookback
    return {"asset_class": "rates", "time_window": {"start_date": "2022-01-01", "end_date": end},
            "sources": {"dtcc_sdr_rates": src}}


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("src.marketdata.rates.dtcc_sdr_downloader.get_output_path",
                        lambda ac, f: tmp_path / f)
    monkeypatch.setattr("src.marketdata.rates.dtcc_sdr_downloader.write_provenance",
                        lambda path, df, source_url="": None)
    return tmp_path


def _downloader(config: dict, zips: dict[str, bytes]) -> tuple[DTCCSDRRatesDownloader, _FakeClient]:
    dl = DTCCSDRRatesDownloader(config, {})
    client = _FakeClient(zips)
    dl._client = client  # the network boundary
    return dl, client


# 19-digit identifiers above 2^53: a numeric parse anywhere would corrupt them.
A, B, C, OPT, EUR = "5611091014000000301", "5612623781000000201", "5612623783000000401", \
    "5612511502000000401", "5612507293000000101"
A_CORR, B_EROR = "5615315077000000201", "5615315077000000999"


def test_keeps_every_same_day_record_with_exact_identifiers(out_dir):
    day1 = [_raw(A), _raw(B), _raw(C),
            _raw(OPT, premium="125000"),               # option -> options file, not swaps
            _raw(EUR, ccy="EUR")]                       # wrong currency -> dropped
    dl, _ = _downloader(_config(end="2026-09-28"), {"2026-09-28": _zip(day1, "2026-09-28")})
    dl.run()

    irs = read_sdr_records(out_dir / "dtcc_sdr_irs.csv")
    assert sorted(irs[DID]) == sorted([A, B, C])        # three trades on one day all kept
    assert irs[ODID].isna().all()
    opts = read_sdr_records(out_dir / "dtcc_sdr_ir_options.csv")
    assert list(opts[DID]) == [OPT]


def test_corrections_and_cancellations_apply_across_runs(out_dir):
    zips = {"2026-09-28": _zip([_raw(A, rate="0.0370"), _raw(B)], "2026-09-28")}
    dl, _ = _downloader(_config(end="2026-09-28"), zips)
    dl.run()
    zips["2026-09-29"] = _zip([
        _raw(A_CORR, action="CORR", orig=A, rate="0.0372", ts="2026-09-29T10:00:00Z"),
        _raw(B_EROR, action="EROR", orig=B, ts="2026-09-29T10:00:00Z"),
    ], "2026-09-29")
    dl, client = _downloader(_config(end="2026-09-29"), zips)
    dl.run()

    assert client.requested == ["2026-09-29"]           # 09-28 already on disk
    irs = read_sdr_records(out_dir / "dtcc_sdr_irs.csv")
    assert list(irs[DID]) == [A_CORR]                    # A replaced by its CORR; B cancelled
    assert list(irs["Fixed rate-Leg 1"]) == ["0.0372"]


def test_legacy_file_is_replaced_and_every_date_refetched(out_dir):
    legacy = pd.DataFrame({"date": ["2026-09-28", "2026-09-29", "2026-09-30"],
                           "Fixed rate-Leg 1": [0.037, 0.038, 0.039], "Action type": ["NEWT"] * 3})
    legacy.to_csv(out_dir / "dtcc_sdr_irs.csv", index=False)
    legacy.to_csv(out_dir / "dtcc_sdr_ir_options.csv", index=False)
    zips = {d: _zip([_raw(f"56000000000000000{i}0")], d)
            for i, d in enumerate(["2026-09-28", "2026-09-29", "2026-09-30"])}
    dl, client = _downloader(_config(), zips)
    dl.run()

    assert client.requested == ["2026-09-28", "2026-09-29", "2026-09-30"]
    irs = read_sdr_records(out_dir / "dtcc_sdr_irs.csv")
    assert DID in irs.columns and len(irs) == 3


def test_rerun_is_idempotent_and_fetches_nothing(out_dir):
    zips = {"2026-09-28": _zip([_raw(A), _raw(B)], "2026-09-28"),
            "2026-09-29": _zip([_raw(C)], "2026-09-29"),
            "2026-09-30": _zip([_raw(OPT, premium="1")], "2026-09-30")}
    dl, _ = _downloader(_config(), zips)
    dl.run()
    before = (out_dir / "dtcc_sdr_irs.csv").read_bytes()
    dl, client = _downloader(_config(), zips)
    dl.run()

    assert client.requested == []
    assert (out_dir / "dtcc_sdr_irs.csv").read_bytes() == before


def test_lookback_window_drops_older_records(out_dir):
    zips = {"2026-09-28": _zip([_raw(A)], "2026-09-28"),
            "2026-09-29": _zip([_raw(B)], "2026-09-29"),
            "2026-09-30": _zip([_raw(C)], "2026-09-30")}
    dl, _ = _downloader(_config(end="2026-09-29"), zips)
    dl.run()
    dl, client = _downloader(_config(end="2026-09-30", lookback=2), zips)
    dl.run()

    assert client.requested == ["2026-09-30"]
    irs = read_sdr_records(out_dir / "dtcc_sdr_irs.csv")
    assert sorted(irs[DID]) == sorted([B, C])            # 09-28 fell out of the 2-day window


def test_cancelled_trade_stays_cancelled_when_its_day_is_fetched_late(out_dir):
    # Review finding (P2): D1's fetch fails, D2 (carrying the EROR for a D1 trade) succeeds; when D1 is
    # retried later its original must not come back as live data.
    d1 = _zip([_raw(A), _raw(B)], "2026-09-28")
    d2 = _zip([_raw(C, ts="2026-09-29T09:00:00Z"),
               _raw(B_EROR, action="EROR", orig=B, ts="2026-09-29T10:00:00Z")], "2026-09-29")
    dl, _ = _downloader(_config(end="2026-09-29"), {"2026-09-29": d2})      # D1 fails this run
    dl.run()
    dl, client = _downloader(_config(end="2026-09-29"), {"2026-09-28": d1, "2026-09-29": d2})
    dl.run()

    assert client.requested == ["2026-09-28"]                               # the late retry
    irs = read_sdr_records(out_dir / "dtcc_sdr_irs.csv")
    assert sorted(irs[DID]) == sorted([A, C])                                # B stays cancelled


def test_cancellations_age_out_with_the_window(out_dir):
    zips = {"2026-09-28": _zip([_raw(A)], "2026-09-28"),
            "2026-09-29": _zip([_raw(B_EROR, action="EROR", orig=A, ts="2026-09-29T10:00:00Z"),
                                _raw(B)], "2026-09-29"),
            "2026-09-30": _zip([_raw(C)], "2026-09-30")}
    dl, _ = _downloader(_config(end="2026-09-29"), zips)
    dl.run()
    assert (out_dir / "dtcc_sdr_irs.csv.cancelled.csv").exists()
    dl, _ = _downloader(_config(end="2026-09-30", lookback=1), zips)
    dl.run()

    cancelled = read_sdr_records(out_dir / "dtcc_sdr_irs.csv.cancelled.csv")
    assert cancelled.empty or list(cancelled["root"]) == []                  # 09-29 left the 1-day window


def test_failed_run_never_overwrites_the_file(out_dir):
    legacy = pd.DataFrame({"date": ["2026-09-28"], "Fixed rate-Leg 1": [0.037]})
    legacy.to_csv(out_dir / "dtcc_sdr_irs.csv", index=False)
    before = (out_dir / "dtcc_sdr_irs.csv").read_bytes()
    dl, client = _downloader(_config(), {})              # every fetch fails
    dl.run()

    assert client.requested == ["2026-09-28", "2026-09-29", "2026-09-30"]
    assert (out_dir / "dtcc_sdr_irs.csv").read_bytes() == before
