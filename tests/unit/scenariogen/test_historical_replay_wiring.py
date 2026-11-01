"""scenario-historical-replay wiring: replay specs in the catalog, the credit match, authoring and the runner.

Oracles are hand values on synthetic series (dyadic moves, so exact in binary); no ``data/``, services or
QuantLib.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from src.scenariogen.authoring import AuthoringError, apply_set, export_current, validate
from src.scenariogen.catalog import REPLAY_UNCOVERED, ScenarioCatalog, resolve_replay
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.long_history import build_long_history
from src.scenariogen.runner import _parse_args
from src.scenariogen.scenario_gen import ScenarioGenerator
from src.scenariogen.versioning import compute_scenario_version

DAYS = ["2025-01-21", "2025-01-22", "2025-01-23", "2025-01-24", "2025-01-27", "2025-01-28"]


def _series() -> dict[str, pd.Series]:
    return {
        "eq:XLK": pd.Series([0.0, 0.5, 0.25, -0.5, 0.125, -0.25], index=DAYS),
        "eq:INTC": pd.Series([0.0, 0.0, 0.0, -0.25, -0.5, 0.0], index=DAYS),
        "ir:DGS10": pd.Series([0.03125, -0.015625], index=DAYS[3:5]),        # history starts inside the window
        "eqvol": pd.Series([0.0, 0.0, 0.0, 0.0625, 0.125, 0.0], index=DAYS),
        "style:MOM": pd.Series([0.0, 0.0, 0.0, 0.25, 0.25, 0.0], index=DAYS),  # not a Σ factor
    }


SPEC = {"narrative": "toy", "method": "drivers_only", "horizon_days": 99, "overlays": {"eqvol": {"move_pts": 5}},
        "severity": {"lead": "eq:XLK", "direction": "down"},
        "replay": {"start": "2025-01-23", "end": "2025-01-27", "idiosyncratic": {"eq:INTC": "earnings miss"}}}
FACTORS = ("eq:XLK", "eq:INTC", "ir:DGS10", "eqvol")


def test_resolve_replay_drivers_horizon_method_and_provenance():
    resolved, info = resolve_replay("toy", SPEC, _series(), FACTORS)
    # (01-23, 01-27]: XLK (1-0.5)(1+0.125)-1 = -0.4375; DGS10 0.03125-0.015625; eqvol 0.0625+0.125.
    assert resolved["drivers"] == {"eq:XLK": {"move": -0.4375}, "eqvol": {"move": 0.1875},
                                   "ir:DGS10": {"move": 0.015625}}
    assert resolved["horizon_days"] == 2                       # eq:XLK rows 01-24, 01-27
    assert resolved["method"] == "conditional_covariance"
    assert resolved["overlays"] == SPEC["overlays"]            # overlays stay as authored
    assert resolved["severity"] == SPEC["severity"]
    assert info["n_observed"] == 3
    # INTC: (1-0.25)(1-0.5)-1 = -0.625, labelled -> reported, not replayed
    assert info["excluded"] == {"eq:INTC": {"move": -0.625, "reason": "earnings miss"}}
    assert info["partial"] == {"ir:DGS10": "2025-01-24"}
    assert (info["start"], info["end"], info["horizon_days"]) == ("2025-01-23", "2025-01-27", 2)
    assert "drivers" not in SPEC                               # the config spec is not mutated


def test_resolve_replay_uncovered_window_is_fail_loud():
    spec = dict(SPEC, replay={"start": "2024-12-02", "end": "2025-01-27"})
    with pytest.raises(ScenarioGenerationError) as ei:
        resolve_replay("toy", spec, _series(), FACTORS)
    assert ei.value.reason == REPLAY_UNCOVERED and ei.value.scenario == "toy"
    with pytest.raises(ScenarioGenerationError):
        resolve_replay("toy", dict(SPEC, replay={"start": "2025-01-23", "end": "2025-02-27"}), _series(), FACTORS)


def _cov() -> FactorCovariance:
    vols = np.array([0.02, 0.03, 0.001, 0.01])
    corr = np.full((4, 4), 0.5) + 0.5 * np.eye(4)
    return FactorCovariance(factors=FACTORS, mean=np.zeros(4), cov=np.outer(vols, vols) * corr,
                            n_obs=250, window="test", cov_id="toy")


def _catalog() -> ScenarioCatalog:
    resolved, info = resolve_replay("toy", SPEC, _series(), FACTORS)
    err = ScenarioGenerationError("gone", REPLAY_UNCOVERED, "outside history")
    return ScenarioCatalog(covariance=_cov(), specs={"toy": resolved, "gone": {"replay": {}}},
                           severity_tiers={"mild": 1.0}, sensitivities={"mom": {"drivers": {"eqvol": 0.01}}},
                           replays={"toy": info}, errors={"gone": err})


def test_catalog_replay_definition_materialize_and_stamp():
    cat = _catalog()
    defn = cat.definition("toy", "mild")
    assert defn.drivers == {"eq:XLK": -0.4375, "eqvol": 0.1875, "ir:DGS10": 0.015625}
    assert defn.horizon_days == 2 and defn.method == "conditional_covariance"
    # the version hashes the resolved economics, so revised data under the window changes it
    assert defn.version == compute_scenario_version(cat.specs["toy"])
    ms = cat.materialize("toy", "mild")
    assert ms.vector["eq:XLK"] == pytest.approx(-0.4375)
    assert ms.vector["eq:INTC"] != pytest.approx(-0.625)        # Σ fills the excluded name
    assert ms.provenance["replay"]["excluded"]["eq:INTC"]["reason"] == "earnings miss"
    plain = ScenarioGenerator(cat.covariance).materialize(cat.definition("mom", "mild"))
    assert "replay" not in cat.stamp("mom", plain).provenance


def test_catalog_sensitivities_and_errors():
    cat = _catalog()
    assert cat.names() == ["toy", "gone"] and cat.sensitivity_names() == ["mom"]
    assert set(cat.scenario_config()["scenarios"]) == {"toy", "gone", "mom"}
    with pytest.raises(ScenarioGenerationError):
        cat.definition("gone", "mild")
    with pytest.raises(KeyError):
        cat.definition("nope", "mild")


def test_runner_sensitivities_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["runner"])
    assert _parse_args().sensitivities is False
    monkeypatch.setattr("sys.argv", ["runner", "--sensitivities"])
    assert _parse_args().sensitivities is True


def test_long_history_match_scales_proxy_to_target_vol():
    dates = pd.bdate_range("2010-01-01", periods=400).strftime("%Y-%m-%d")
    baa = np.cumsum(np.tile([0.02, -0.01, 0.03, -0.02], 100)) + 2.0       # levels in percent
    oas = pd.Series(np.cumsum(np.tile([0.01, -0.005, 0.015, -0.01], 100)) + 1.0, index=dates)
    fred = pd.DataFrame({"date": dates, "BAA": baa, "OAS": oas.where(np.arange(400) >= 100).to_numpy()})
    series, info = build_long_history(pd.DataFrame(columns=["date", "ticker", "adj_close"]), fred,
                                      {"credit": {"fred": "BAA", "match": "OAS"}}, min_overlap=250)
    # OAS steps are exactly half the BAA steps on the shared dates, so beta = 0.5 and every move halves.
    assert info["credit"]["beta"] == pytest.approx(0.5, rel=1e-9)
    assert info["credit"]["matched"] == "OAS"
    assert series["credit"].iloc[0] == pytest.approx(0.5 * (-0.01) / 100.0)
    assert series["credit"].index[0] == dates[1]                         # whole long series kept
    with pytest.raises(ValueError, match="OAS"):
        build_long_history(pd.DataFrame(columns=["date", "ticker", "adj_close"]), fred.drop(columns="OAS"),
                           {"credit": {"fred": "BAA", "match": "OAS"}})


def _doc() -> dict:
    return {"scenarios": {"toy_replay": {
        "narrative": "toy replay", "required_services": ["arag"],
        "replay": {"start": "2025-01-23", "end": "2025-01-27", "idiosyncratic": {"eq:INTC": "miss"}},
        "severity": {"lead": "eq:XLK", "direction": "down"},
        "companies": [{"ticker": "NVDA", "narrative_query": "q"}]}}}


def test_authoring_validates_replays():
    validate(_doc())                                   # no drivers, company seeded by the episode
    bad = _doc()
    bad["scenarios"]["toy_replay"]["drivers"] = {"eq:XLK": {"move_pct": -10}}
    bad["scenarios"]["toy_replay"]["replay"] = {"start": "2025-01-23"}
    with pytest.raises(AuthoringError) as ei:
        validate(bad)
    assert len(ei.value.errors) == 2


def test_authoring_apply_keeps_replay_and_env_placeholders(tmp_path):
    d = tmp_path / "configs" / "scenariogen"
    d.mkdir(parents=True)
    conn = {"enabled": True, "base_url": "${MACRORAG_BASE_URL:-http://localhost:8000}"}
    (d / "scenarios.json").write_text(json.dumps({"scenarios": {}, "sensitivities": {"s": {}}}))
    (d / "macro.json").write_text(json.dumps({"connection": conn, "scenarios": {}}))
    (d / "arag.json").write_text(json.dumps({"connection": dict(conn, base_url="${ARAG_BASE_URL:-x}"),
                                             "scenarios": {}}))
    apply_set(_doc(), tmp_path, replace_set=True)
    scen = json.loads((d / "scenarios.json").read_text())
    assert scen["scenarios"]["toy_replay"]["replay"]["idiosyncratic"] == {"eq:INTC": "miss"}
    assert scen["sensitivities"] == {"s": {}}                              # other blocks preserved
    assert json.loads((d / "macro.json").read_text())["connection"]["base_url"] == conn["base_url"]
    assert json.loads((d / "arag.json").read_text())["connection"]["base_url"] == "${ARAG_BASE_URL:-x}"
    assert export_current(tmp_path)["scenarios"]["toy_replay"]["replay"]["start"] == "2025-01-23"
