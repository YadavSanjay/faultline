"""Tests for the F9 physical-throughput scenario calibration (market-intelligence P7).

Data-free: the provider is built from in-memory monthly series. Covers the YoY growth, the
inverse (low-growth-amplifies) multiplier, the contraction trigger, PIT resolution, and the
EIA power aggregation.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.scenariogen.throughput import ThroughputConfig, ThroughputProvider, _read_power

CFG = ThroughputConfig(beta=0.35, min_scale=0.85, max_scale=1.4, yoy_months=12,
                       trigger_low_percentile=0.15)


def _monthly(values: list[float], start_year: int = 2000) -> list[tuple[str, float]]:
    return [(f"{start_year + i // 12:04d}-{(i % 12) + 1:02d}-01", v) for i, v in enumerate(values)]


def _prov(ipg_values: list[float]) -> ThroughputProvider:
    return ThroughputProvider(_monthly(ipg_values), [], CFG)


def test_booming_throughput_damps_the_bust():
    # steadily rising IPG -> latest YoY is high vs history -> high percentile -> damp toward min_scale
    prov = _prov([100.0 * (1.02 ** i) for i in range(60)])   # +2%/mo compounding
    r = prov.resolve(date(2099, 12, 31))
    assert r.applied
    assert r.ipg_yoy is not None and r.ipg_yoy > 0
    assert r.thesis in ("intact", "cooling")
    assert r.multiplier < 1.0                                # accelerating thesis -> damp
    assert not r.trigger


def test_contracting_throughput_amplifies_and_triggers():
    # 4 years rising then a sharp fall -> latest YoY < 0 -> amplify + trigger (thesis breaking)
    vals = [100.0 + i for i in range(48)] + [140.0, 120.0, 100.0, 80.0, 60.0, 40.0]
    r = _prov(vals).resolve(date(2099, 12, 31))
    assert r.ipg_yoy is not None and r.ipg_yoy < 0
    assert r.trigger and r.thesis == "breaking"
    assert r.multiplier > 1.0                                # low growth percentile -> amplify


def test_multiplier_is_inverse_of_growth_percentile():
    # a flat series with the latest exactly at the median YoY -> multiplier ~ 1.0
    vals = [100.0 + (i % 6) for i in range(48)]
    r = _prov(vals).resolve(date(2099, 12, 31))
    assert r.ipg_percentile is not None
    # 1 + beta*(1 - 2*pct) evaluated at the resolved percentile, then clamped
    expected = max(CFG.min_scale, min(CFG.max_scale, 1.0 + CFG.beta * (1.0 - 2.0 * r.ipg_percentile)))
    assert r.multiplier == pytest.approx(expected)


def test_pit_excludes_future_and_holds_when_thin():
    prov = _prov([100.0 + i for i in range(30)])
    # only 13 months of data <= as_of -> 1 YoY obs -> held (needs >= 12 YoY obs)
    held = prov.resolve(date(2001, 1, 1))
    assert not held.applied
    ok = prov.resolve(date(2099, 12, 31))
    assert ok.applied


def test_disabled_or_absent_yields_no_provider(tmp_path):
    assert ThroughputProvider.load(tmp_path, {"enabled": False}) is None
    assert ThroughputProvider.load(tmp_path, {"enabled": True}) is None    # no feed on disk


def test_power_aggregation_sums_respondents_by_month(tmp_path):
    p = tmp_path / "data" / "marketdata" / "stress" / "eia_power_demand.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "date,respondent,demand_mwh\n"
        "2026-01-01,CISO,100\n2026-01-01,PJM,200\n2026-01-02,CISO,110\n2026-01-02,PJM,210\n"
        "2026-01-01,XXXX,999\n",          # not in the respondent set -> excluded
        encoding="utf-8")
    series = _read_power(str(p), ("CISO", "PJM"))
    # Jan 2026: daily totals 300 and 320 -> monthly mean 310
    assert series == [("2026-01-01", 310.0)]
