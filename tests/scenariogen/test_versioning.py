"""S3 versioning tests: content-addressed scenario version + governed store."""

from __future__ import annotations

import pytest
from src.scenariogen.scenario_gen import ScenarioDefinition
from src.scenariogen.versioning import ScenarioStore, compute_scenario_version

SPEC = {
    "narrative": "Taiwan crisis",
    "trigger": "BIS rule",
    "method": "conditional_covariance",
    "horizon_days": 10,
    "drivers": {"fx:TWD": {"move_pct": 8}, "eq:TSM": {"move_pct": -25}},
    "overlays": {"eqvol": {"move_pts": 5}},
}


def test_version_is_content_addressed():
    v = compute_scenario_version(SPEC)
    assert len(v) == 16
    assert compute_scenario_version(SPEC) == v                      # deterministic
    # documentation-only edits don't churn the version
    assert compute_scenario_version({**SPEC, "narrative": "different doc", "trigger": "x"}) == v
    # an economic edit (driver magnitude) does
    bumped = {**SPEC, "drivers": {"fx:TWD": {"move_pct": 15}, "eq:TSM": {"move_pct": -25}}}
    assert compute_scenario_version(bumped) != v


def test_from_config_stamps_version():
    defn = ScenarioDefinition.from_config("taiwan", SPEC)
    assert defn.version == compute_scenario_version(SPEC)


def test_store_register_idempotent_and_loadable(tmp_path):
    store = ScenarioStore(tmp_path)
    v1 = store.register("taiwan", SPEC)
    v2 = store.register("taiwan", SPEC)                              # idempotent
    assert v1 == v2
    loaded = store.load_version(v1)
    assert loaded["scenario_version"] == v1 and loaded["name"] == "taiwan"
    assert loaded["drivers"] == SPEC["drivers"]
    versions = store.versions()
    assert len(versions) == 1 and versions.iloc[0]["scenario_version"] == v1
    assert int(versions.iloc[0]["n_drivers"]) == 2


def test_store_tracks_two_versions(tmp_path):
    store = ScenarioStore(tmp_path)
    v1 = store.register("taiwan", SPEC)
    v2 = store.register("taiwan_worse",
                        {**SPEC, "drivers": {"fx:TWD": {"move_pct": 20}}})
    assert v1 != v2
    assert set(store.versions()["scenario_version"]) == {v1, v2}


def test_load_unknown_version_raises(tmp_path):
    with pytest.raises(KeyError):
        ScenarioStore(tmp_path).load_version("deadbeefdeadbeef")
