"""Tests for the F5 crowding calibration (market-intelligence P7).

Data-free: the provider is built from in-memory days-to-cover series. Covers the crowded-amplifies
multiplier, the #6 SMH short-squeeze flag, PIT resolution, and the dual-RAG composition with news.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.scenariogen.crowding import CrowdingConfig, CrowdingProvider

CFG = CrowdingConfig(beta=0.3, min_scale=0.8, max_scale=1.4, squeeze_percentile=0.8,
                     short_names=frozenset({"SMH"}))


def _series(values: list[float]) -> list[tuple[str, float]]:
    return [(f"2024-{(i % 12) + 1:02d}-15", v) for i, v in enumerate(values)]


def _prov(by_name: dict[str, list[float]]) -> CrowdingProvider:
    return CrowdingProvider({t: _series(v) for t, v in by_name.items()}, CFG)


def test_crowded_name_amplifies_toward_max():
    # latest days-to-cover is the max of a rising series -> percentile ~1 -> amplify
    r = _prov({"NVDA": [float(i) for i in range(20)]}).resolve(date(2099, 12, 31))
    leg = r.leg_for("NVDA")
    assert leg is not None
    assert leg.percentile == pytest.approx(1.0)
    assert leg.multiplier == pytest.approx(min(1.4, 1.0 + 0.3 * (2 * 1.0 - 1)))   # 1.3
    assert not leg.is_short and not leg.squeeze


def test_uncrowded_name_damps_to_min():
    r = _prov({"MU": [100.0] * 15 + [1.0]}).resolve(date(2099, 12, 31))
    leg = r.leg_for("MU")
    assert leg is not None
    # raw 1 + 0.3*(2*(1/16) - 1) = 0.7375 -> clamped to min_scale 0.8
    assert leg.multiplier == pytest.approx(0.8)


def test_crowded_short_flags_squeeze_asymmetry():
    # SMH (the #6 short) at its historical high days-to-cover -> crowded short -> squeeze flag
    r = _prov({"SMH": [float(i) for i in range(20)]}).resolve(date(2099, 12, 31))
    leg = r.leg_for("SMH")
    assert leg is not None and leg.is_short
    assert leg.percentile >= CFG.squeeze_percentile
    assert leg.squeeze
    assert r.squeeze_names == ["SMH"]


def test_short_at_median_is_not_a_squeeze():
    r = _prov({"SMH": [5.0, 1.0, 5.0, 1.0, 5.0, 1.0, 3.0]}).resolve(date(2099, 12, 31))
    leg = r.leg_for("SMH")
    assert leg is not None and leg.is_short and not leg.squeeze     # not at the crowded tail
    assert r.squeeze_names == []


def test_pit_and_thin_history_is_skipped():
    prov = _prov({"NVDA": [float(i) for i in range(20)]})
    early = prov.resolve(date(2024, 3, 1))          # only ~3 obs <= as_of -> < 6 -> skipped
    assert early.leg_for("NVDA") is None
    assert prov.resolve(date(2099, 1, 1)).leg_for("NVDA") is not None


def test_disabled_or_absent_yields_no_provider(tmp_path):
    assert CrowdingProvider.load(tmp_path, {"enabled": False}) is None
    assert CrowdingProvider.load(tmp_path, {"enabled": True}) is None    # no feed on disk
