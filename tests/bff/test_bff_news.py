"""BFF news & events reads (P6/A5): `/api/news/events`, `/calibration`, `/{ticker}`."""

from __future__ import annotations

from pathlib import Path

from bff_helpers import write_feed_csv
from ui.bff.app import news


def _seed(root: Path) -> None:
    write_feed_csv(root, "news/sec_events.csv",
                   ["ticker", "cik", "filing_date", "form", "item_codes", "accession", "doc_url", "weight"], [
                       ["NVDA", "1", "2026-07-02", "8-K", "5.02", "a-1", "u1", 1.0],
                       ["NVDA", "1", "2026-05-20", "8-K", "2.02", "a-2", "u2", 0.6],
                       ["MU", "2", "2026-06-15", "8-K", "5.02", "a-3", "u3", 1.0],
                       ["ORCL", "3", "2026-06-30", "8-K", "8.01", "a-4", "u4", 0.2],
                   ])
    write_feed_csv(root, "news/gdelt_salience.csv",
                   ["ticker", "date", "article_volume", "avg_tone", "vol_z_63d", "tone_z_63d"], [
                       ["NVDA", "2026-07-05", "0.03", "-1.0", "0.5", "-1.5"],
                   ])


def test_events_stream_decodes_item_labels(bff_root: Path) -> None:
    _seed(bff_root)
    out = news.list_events()
    assert out["total"] == 4
    assert set(out["tickers"]) == {"NVDA", "MU", "ORCL"}
    top = out["events"][0]                               # newest first -> NVDA 2026-07-02
    assert top["ticker"] == "NVDA" and top["filing_date"] == "2026-07-02"
    assert top["items"] == [{"code": "5.02", "label": "Departure of directors / officers"}]
    assert all(isinstance(e["weight"], float) for e in out["events"])  # UI calls .toFixed on it


def test_events_filter_by_ticker(bff_root: Path) -> None:
    _seed(bff_root)
    out = news.list_events(ticker="mu")                  # case-insensitive
    assert out["count"] == 1 and out["events"][0]["ticker"] == "MU"


def test_calibration_resolves_gate_multiplier_and_triggers(bff_root: Path) -> None:
    _seed(bff_root)
    c = news.calibration("2026-07-10")
    assert c["applied"] is True
    assert c["legs"]["NVDA"]["multiplier"] > 1.0         # events + negative tone
    assert set(c["live_triggers"]["5.02"]) == {"NVDA", "MU"}   # not ORCL (8.01)
    assert c["live_trigger_labels"]["5.02"] == "Departure of directors / officers"
    assert c["earnings"]["NVDA"] == ["2026-05-20"]


def test_name_detail_has_events_salience_and_leg(bff_root: Path) -> None:
    _seed(bff_root)
    d = news.name_detail("NVDA", "2026-07-10")
    assert d is not None
    assert len(d["events"]) == 2
    assert len(d["salience"]) == 1
    assert d["leg"]["multiplier"] > 1.0
    assert news.name_detail("ZZZZ", "2026-07-10") is None


def test_empty_when_no_feed(bff_root: Path) -> None:
    out = news.list_events()
    assert out["count"] == 0 and out["events"] == []
    assert news.calibration()["applied"] is False
