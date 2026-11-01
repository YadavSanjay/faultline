"""Tests for the F8 geopolitical (GPR) scenario calibration (market-intelligence P7).

Unit tests build the provider from an in-memory series (data-free); one integration test uses the
on-disk GPR feed + real catalog to confirm the severity multiplier scales a tagged scenario.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.scenariogen.geopolitical import GeoConfig, GeopoliticalProvider

CFG = GeoConfig(beta=0.4, baseline_percentile=0.5, min_scale=0.85, max_scale=1.4,
                trigger_z=1.5, trigger_percentile=0.9)


def _prov(values: list[float], series: str = "GPRC_TWN") -> GeopoliticalProvider:
    # monthly dates ascending, one value each
    pts = [(f"2020-{(i % 12) + 1:02d}-01" if i < 12 else f"20{20 + i // 12:02d}-{(i % 12) + 1:02d}-01", v)
           for i, v in enumerate(values)]
    # ensure strictly increasing date strings
    pts = [(f"{2000 + i // 12:04d}-{(i % 12) + 1:02d}-01", v) for i, v in enumerate(values)]
    return GeopoliticalProvider({series: pts}, CFG)


def test_percentile_and_multiplier_at_the_top():
    # latest is the max of a long rising series -> percentile ~1.0 -> amplify toward max_scale
    prov = _prov([float(i) for i in range(50)])
    r = prov.resolve("GPRC_TWN", date(2099, 12, 31))
    assert r.applied
    assert r.percentile == pytest.approx(1.0)
    # 1 + 0.4*(2*1 - 1) = 1.4 -> clamped at max_scale 1.4
    assert r.multiplier == pytest.approx(1.4)
    assert r.trigger                                        # percentile 1.0 >= 0.9


def test_low_regime_damps_only_to_min_scale():
    # latest is the min -> percentile small -> mild damp, floored at min_scale
    prov = _prov([100.0] * 40 + [1.0])
    r = prov.resolve("GPRC_TWN", date(2099, 12, 31))
    assert r.multiplier == pytest.approx(0.85)              # floored
    assert not r.trigger


def test_trigger_via_zscore_even_if_percentile_below_threshold():
    # a big spike: z >= trigger_z fires even when the percentile threshold might not
    prov = _prov([1.0] * 60 + [50.0])
    r = prov.resolve("GPRC_TWN", date(2099, 12, 31))
    assert r.z >= CFG.trigger_z
    assert r.trigger


def test_pit_excludes_future_observations():
    prov = _prov([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 99.0])
    # as_of before the 99.0 spike (13th point, 2001-01) -> latest used is 12.0, not 99.0
    r = prov.resolve("GPRC_TWN", date(2000, 12, 31))
    assert r.level == pytest.approx(12.0)
    assert r.n_obs == 12


def test_held_when_thin_or_series_absent():
    thin = GeopoliticalProvider({"GPRC_TWN": [("2020-01-01", 1.0)]}, CFG)
    assert not thin.resolve("GPRC_TWN", date(2099, 1, 1)).applied      # < 12 obs
    prov = _prov([float(i) for i in range(50)])
    r = prov.resolve("GPRC_XXX", date(2099, 1, 1))
    assert not r.applied and r.multiplier == 1.0                        # unknown series -> no-op


def test_disabled_config_yields_no_provider(tmp_path):
    assert GeopoliticalProvider.load(tmp_path, {"enabled": False}) is None
    assert GeopoliticalProvider.load(tmp_path, {"enabled": True}) is None   # no feed on disk


# --- integration: real catalog + on-disk GPR feed -------------------------- #
@pytest.mark.needs_market_data
def test_severity_multiplier_scales_a_tagged_scenario():
    from src.pricer.config import get_project_root, load_pricer_config
    from src.pricer.risk.factor_history import FactorHistory
    from src.scenariogen.catalog import ScenarioCatalog

    root = get_project_root()
    cat = ScenarioCatalog.from_config(FactorHistory(root, load_pricer_config()), project_root=root)
    if "taiwan_geopolitical" not in cat.names():
        pytest.skip("taiwan_geopolitical not applied")
    base = cat.materialize("taiwan_geopolitical", "adverse", severity_multiplier=1.0)
    up = cat.materialize("taiwan_geopolitical", "adverse", severity_multiplier=1.3)
    # A pure driver (no overlay component) scales by exactly 1.3 — the severity multiplier acts on
    # the driver magnitudes, exactly like a tier multiplier (overlays and propagated legs differ).
    assert "fx:TWD" in base.vector
    assert up.vector["fx:TWD"] == pytest.approx(base.vector["fx:TWD"] * 1.3, rel=1e-6)
    assert up.vector != base.vector                         # the shock genuinely moved
