"""Tests for the scenariogen news consumption (A3/A4, market-intelligence P3).

Offline: the provider is built from small in-memory frames written under a temp data dir, so
nothing touches the real feeds or the network. Covers the clamped multiplier + age decay, the
freshness/coverage gate, PIT resolution, the 5.02 keyman trigger, and the ToolSource adapter.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.scenariogen.news.gate import NewsGateConfig, check_gate
from src.scenariogen.news.mapping import EventRow, NewsMapConfig, event_score, news_multiplier
from src.scenariogen.news.provider import NewsEventProvider

CFG = NewsMapConfig(beta_event=0.15, beta_tone=0.10, window_days=90,
                    decay_halflife_days=30.0, min_scale=0.8, max_scale=1.5)


def _ev(d: str, items: str, w: float, acc: str = "acc") -> EventRow:
    return EventRow(filing_date=d, item_codes=items, accession=acc, weight=w)


# --- mapping --------------------------------------------------------------- #
def test_event_score_decays_with_age():
    as_of = date(2026, 7, 10)
    fresh = event_score([_ev("2026-07-10", "5.02", 1.0)], as_of, CFG)[0]     # age 0 -> full weight
    old = event_score([_ev("2026-06-10", "5.02", 1.0)], as_of, CFG)[0]       # age 30 -> half-life
    assert fresh == pytest.approx(1.0)
    assert old == pytest.approx(0.5, abs=1e-6)


def test_events_outside_window_are_ignored():
    as_of = date(2026, 7, 10)
    score, in_win = event_score([_ev("2026-01-01", "5.02", 1.0)], as_of, CFG)   # ~190d > 90d window
    assert score == 0.0 and in_win == []


def test_multiplier_composes_event_and_tone_and_clamps():
    as_of = date(2026, 7, 10)
    leg = news_multiplier("NVDA", [_ev("2026-07-10", "5.02", 1.0)], tone_z=-2.0, as_of=as_of, cfg=CFG)
    # 1 + 0.15*1.0 + 0.10*(-(-2.0)) = 1.35
    assert leg.multiplier == pytest.approx(1.35)
    assert "5.02" in leg.item_codes and leg.n_events == 1
    # a huge score clamps at max_scale
    big = news_multiplier("NVDA", [_ev("2026-07-10", "5.02", 10.0)], tone_z=None, as_of=as_of, cfg=CFG)
    assert big.multiplier == pytest.approx(1.5)
    # positive tone (calm) can pull it below 1 but not past min_scale
    calm = news_multiplier("NVDA", [], tone_z=10.0, as_of=as_of, cfg=CFG)
    assert calm.multiplier == pytest.approx(0.8)


# --- gate ------------------------------------------------------------------ #
def test_gate_holds_on_stale_or_thin():
    cfg = NewsGateConfig(max_staleness_days=200, min_names_with_events=3)
    assert check_gate("2026-07-09", 9, date(2026, 7, 10), cfg).ok
    assert not check_gate(None, 0, date(2026, 7, 10), cfg).ok                 # no events
    assert not check_gate("2025-01-01", 9, date(2026, 7, 10), cfg).ok         # stale
    assert not check_gate("2026-07-09", 2, date(2026, 7, 10), cfg).ok         # thin


# --- provider (PIT, triggers) ---------------------------------------------- #
def _write(root, rel: str, header: list[str], rows: list[list]) -> None:
    p = root / "data" / "marketdata" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(header)] + [",".join(str(c) for c in r) for r in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed(root):
    _write(root, "news/sec_events.csv",
           ["ticker", "cik", "filing_date", "form", "item_codes", "accession", "doc_url", "weight"], [
               ["NVDA", "1", "2026-07-02", "8-K", "5.02", "a-1", "u", 1.0],
               ["NVDA", "1", "2026-05-20", "8-K", "2.02", "a-2", "u", 0.6],
               ["MU", "2", "2026-06-15", "8-K", "5.02", "a-3", "u", 1.0],
               ["ORCL", "3", "2026-06-30", "8-K", "8.01", "a-4", "u", 0.2],
               # a future filing must be excluded by the PIT filter
               ["NVDA", "1", "2026-08-01", "8-K", "5.02", "a-5", "u", 1.0],
           ])
    _write(root, "news/gdelt_salience.csv",
           ["ticker", "date", "article_volume", "avg_tone", "vol_z_63d", "tone_z_63d"], [
               ["NVDA", "2026-07-05", 0.03, -1.0, 0.5, -1.5],
           ])


def test_provider_resolves_pit_with_triggers(tmp_path):
    _seed(tmp_path)
    bff_root = tmp_path
    cfg = {"enabled": True, "beta_event": 0.15, "beta_tone": 0.10, "window_days": 90,
           "decay_halflife_days": 30.0, "min_scale": 0.8, "max_scale": 1.5,
           "keyman_items": ["5.02"], "earnings_items": ["2.02"],
           "gate": {"max_staleness_days": 200, "min_names_with_events": 2}}
    prov = NewsEventProvider.load(bff_root, cfg)
    assert prov is not None
    res = prov.resolve(date(2026, 7, 10))

    assert res.applied                                   # gate ok (3 names, fresh)
    # the future NVDA 8-K (2026-08-01) is excluded PIT
    assert res.leg_for("NVDA").n_events == 2
    assert res.multiplier_for("NVDA") > 1.0              # events + negative tone raise it
    # 5.02 keyman trigger fires for NVDA and MU, not ORCL (8.01)
    assert set(res.live_triggers["5.02"]) == {"NVDA", "MU"}
    assert res.earnings.get("NVDA") == ["2026-05-20"]
    # unknown name -> 1.0 (no-op)
    assert res.multiplier_for("ZZZZ") == 1.0


def test_provider_held_gate_is_a_noop(tmp_path):
    bff_root = tmp_path
    _seed(bff_root)
    cfg = {"enabled": True, "gate": {"max_staleness_days": 200, "min_names_with_events": 99}}
    res = NewsEventProvider.load(bff_root, cfg).resolve(date(2026, 7, 10))
    assert not res.applied
    assert res.multiplier_for("NVDA") == 1.0             # held -> every multiplier is 1.0


def test_provider_none_when_disabled_or_absent(tmp_path):
    bff_root = tmp_path
    assert NewsEventProvider.load(bff_root, {"enabled": False}) is None   # disabled
    assert NewsEventProvider.load(tmp_path, {"enabled": True}) is None     # no feed on disk


# --- A4 tool source -------------------------------------------------------- #
def test_tool_source_lists_and_calls(tmp_path):
    bff_root = tmp_path
    from src.scenariogen.news.provider import NewsEventProvider as P
    from src.scenariogen.news.tool_source import NewsToolSource

    _seed(bff_root)
    prov = P.load(bff_root, {"enabled": True, "keyman_items": ["5.02"], "earnings_items": ["2.02"],
                             "gate": {"max_staleness_days": 200, "min_names_with_events": 2}})
    ts = NewsToolSource(prov, date(2026, 7, 10))
    tools = ts.list_tools("news")
    assert len(tools) == 1 and tools[0].name == "news_events"
    assert ts.list_tools("other") == []

    import json
    payload = json.loads(ts.call_tool("news", "news_events", {"ticker": "NVDA"}))
    assert payload["ticker"] == "NVDA"
    assert payload["multiplier"] > 1.0
    assert payload["news"]["n_events"] == 2
    assert payload["earnings_dates"] == ["2026-05-20"]
    with pytest.raises(KeyError):
        ts.call_tool("news", "bogus", {})
