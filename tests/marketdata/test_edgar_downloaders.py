"""EDGAR downloaders: fetch/parse, restatement de-dup, and the fail-loud floor — no network.

Both downloaders build an ``HttpClient`` internally but accept an injected ``client`` so the
fetch and floor logic run deterministically against a fake. The floor is the F1 defence: a
download that completes but returns too little usable data (e.g. every SEC request 403'd)
raises ``MarketDataIngestError`` instead of overwriting good data with nothing.
"""

from __future__ import annotations

import json

import pytest
from src.marketdata.bonds.edgar_downloader import EDGARDownloader
from src.marketdata.common.config_loader import get_project_root
from src.marketdata.common.errors import MarketDataIngestError
from src.marketdata.common.http_client import HttpClientError
from src.marketdata.reference.edgar_xbrl_downloader import EDGARXBRLDownloader


class FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def json(self):
        return self._json


class FakeClient:
    """Returns/raises whatever ``handler(endpoint)`` yields; records the calls."""

    def __init__(self, handler):
        self._handler = handler
        self.calls: list[str] = []

    def get(self, endpoint, params=None, extra_headers=None):
        self.calls.append(endpoint)
        item = self._handler(endpoint)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)


@pytest.fixture
def captured(monkeypatch):
    """Redirect both downloaders' writes into an in-memory capture (no disk / no provenance)."""
    box: dict[str, object] = {}

    def _fake_out(asset_class, filename):
        return f"{asset_class}/{filename}"

    def _fake_save(df, path):
        box["path"] = path
        box["df"] = df

    def _fake_upsert(df, path, date_col="date", key_cols=None):
        box["path"] = path
        box["df"] = df

    monkeypatch.setattr("src.marketdata.reference.edgar_xbrl_downloader.get_output_path", _fake_out)
    monkeypatch.setattr("src.marketdata.reference.edgar_xbrl_downloader.upsert_csv", _fake_upsert)
    monkeypatch.setattr("src.marketdata.bonds.edgar_downloader.get_output_path", _fake_out)
    monkeypatch.setattr("src.marketdata.bonds.edgar_downloader.save_csv", _fake_save)
    return box


# --------------------------------------------------------------------------- #
# EDGAR XBRL (reference) downloader
# --------------------------------------------------------------------------- #

def _xbrl_config(min_expected=10):
    return {
        "asset_class": "reference",
        "time_window": {"start_date": "2023-01-01", "end_date": "2026-12-31"},
        "sources": {"edgar_xbrl": {
            "concept_template": "/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json",
            "output_file": "edgar_xbrl_facts.csv",
            "min_expected_facts": min_expected,
            "issuers": [{"ticker": "NVDA", "cik": "0001045810"}],
            "tags": {"Revenues": "Revenue"},
        }},
    }


def _fact(start, end, val, filed, fy=2025, fp="FY", form="10-K"):
    return {"start": start, "end": end, "val": val, "fy": fy, "fp": fp,
            "form": form, "frame": "", "filed": filed}


def test_xbrl_happy_path_parses_and_writes(captured):
    payload = {"label": "Revenues", "units": {"USD": [
        _fact("2024-02-01", "2025-01-31", 130_000, "2025-02-20"),
        _fact("2023-02-01", "2024-01-31", 60_000, "2024-02-21"),
    ]}}
    dl = EDGARXBRLDownloader(_xbrl_config(min_expected=1), {},
                             client=FakeClient(lambda ep: payload))
    out = dl.run()
    assert len(out) == 2
    assert set(out["ticker"]) == {"NVDA"}
    assert captured["df"] is out  # written, not silently dropped


def test_xbrl_restatement_keeps_latest_filed(captured):
    # Same (ticker, tag, start, end); later `filed` restates the value.
    payload = {"label": "Revenues", "units": {"USD": [
        _fact("2024-02-01", "2025-01-31", 100_000, "2025-02-20"),
        _fact("2024-02-01", "2025-01-31", 130_000, "2025-08-15"),  # restatement
    ]}}
    dl = EDGARXBRLDownloader(_xbrl_config(min_expected=1), {},
                             client=FakeClient(lambda ep: payload))
    out = dl.run()
    assert len(out) == 1
    assert float(out["val"].iloc[0]) == 130_000


def test_xbrl_all_requests_fail_raises(captured):
    # Every (issuer, tag) 403s -> no frames -> fail loud, nothing written.
    dl = EDGARXBRLDownloader(
        _xbrl_config(), {},
        client=FakeClient(lambda ep: HttpClientError("HTTP 403")))
    with pytest.raises(MarketDataIngestError):
        dl.run()
    assert "df" not in captured


def test_xbrl_below_floor_raises(captured):
    # A single fact clears parsing but falls under the floor of 10 -> refuse to overwrite.
    payload = {"label": "Revenues", "units": {"USD": [
        _fact("2024-02-01", "2025-01-31", 130_000, "2025-02-20"),
    ]}}
    dl = EDGARXBRLDownloader(_xbrl_config(min_expected=10), {},
                             client=FakeClient(lambda ep: payload))
    with pytest.raises(MarketDataIngestError):
        dl.run()
    assert "df" not in captured


# --------------------------------------------------------------------------- #
# EDGAR bonds (submissions) downloader
# --------------------------------------------------------------------------- #

def _bonds_config(min_expected=3):
    return {
        "asset_class": "bonds",
        "portfolio_bonds": {"corporate": [
            {"issuer": "Oracle", "cik": "0001341439"},
            {"issuer": "Amazon", "cik": "0001018724"},
        ]},
        "sources": {"edgar": {
            "endpoint_template": "/submissions/CIK{cik}.json",
            "form_type_filter": ["424B5", "424B2"],
            "output_file": "edgar_bond_filings.csv",
            "min_expected_filings": min_expected,
        }},
    }


def _submissions(forms):
    n = len(forms)
    return {"filings": {"recent": {
        "form": forms,
        "filingDate": ["2025-03-01"] * n,
        "accessionNumber": [f"0000000000-25-00000{i}" for i in range(n)],
        "primaryDocument": ["d.htm"] * n,
    }}}


def test_bonds_happy_path_filters_forms(captured):
    payload = _submissions(["424B5", "8-K", "424B2"])  # 8-K filtered out
    dl = EDGARDownloader(_bonds_config(min_expected=1), {},
                         client=FakeClient(lambda ep: payload))
    out = dl.run()
    assert set(out["form_type"]) <= {"424B5", "424B2"}
    assert "filing_url" in out.columns
    assert captured["df"] is out


def test_bonds_all_requests_fail_raises(captured):
    dl = EDGARDownloader(
        _bonds_config(), {},
        client=FakeClient(lambda ep: HttpClientError("HTTP 403")))
    with pytest.raises(MarketDataIngestError):
        dl.run()
    assert "df" not in captured


def test_bonds_below_floor_raises(captured):
    # Only one issuer returns a single matching filing -> under the floor of 3.
    def handler(ep):
        if "1341439" in ep:  # Oracle
            return _submissions(["424B5"])
        return _submissions(["8-K"])  # Amazon: nothing matches the filter
    dl = EDGARDownloader(_bonds_config(min_expected=3), {},
                         client=FakeClient(handler))
    with pytest.raises(MarketDataIngestError):
        dl.run()
    assert "df" not in captured


# --------------------------------------------------------------------------- #
# F2 deprecation decision — edgar_xbrl retired in favour of ARag facts_lookup
# --------------------------------------------------------------------------- #

def test_edgar_xbrl_feed_is_deprecated_and_ungated():
    root = get_project_root()
    ref = json.loads((root / "configs" / "marketdata" / "reference"
                      / "reference_marketdata.json").read_text(encoding="utf-8"))
    src = ref["sources"]["edgar_xbrl"]
    assert src["enabled"] is False              # no longer refreshed (F2)
    assert "_deprecated" in src                 # decision is documented in-config

    gate = json.loads((root / "configs" / "marketdata"
                       / "dq_gate.json").read_text(encoding="utf-8"))
    assert not any("edgar_xbrl" in f for f in gate["files"])   # de-listed from the DQ gate
    assert any("edgar_bond_filings" in f for f in gate["files"])  # active bond feed still gated
