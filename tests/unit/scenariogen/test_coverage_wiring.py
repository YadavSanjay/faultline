"""scenario-set-coverage wiring: the config model, the scenario matrix from a stress report and the report body.

Synthetic matrices with hand values; no ``data/``, services or QuantLib.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from src.common.config.models import ConfigError
from src.scenariogen.coverage_runner import CoverageConfig, _parse_args, build_report, scenario_matrix

TRADES = ["T1", "T2", "T3"]
U1 = np.array([1.0, 1.0, 0.0]) / np.sqrt(2.0)        # book direction: both stocks together
U2 = np.array([1.0, -1.0, 0.0]) / np.sqrt(2.0)       # pair direction
U3 = np.array([0.0, 0.0, 1.0])                       # rates sleeve


def _history() -> np.ndarray:
    # Orthogonal +-1 score series (Hadamard columns) with standard deviations 4, 2, 1 along U1, U2, U3.
    h = np.array([[1, 1, 1, 1, 1, 1, 1, 1], [1, -1, 1, -1, 1, -1, 1, -1], [1, 1, -1, -1, 1, 1, -1, -1],
                  [1, -1, -1, 1, 1, -1, -1, 1]], dtype=float).T
    return 4.0 * np.outer(h[:, 1], U1) + 2.0 * np.outer(h[:, 2], U2) + 1.0 * np.outer(h[:, 3], U3)


def test_committed_config_loads_and_unknown_keys_raise(tmp_path):
    cfg = CoverageConfig.load()
    assert (cfg.tier, cfg.variance_share, cfg.max_components) == ("mild", 0.9, 10)
    assert (cfg.uncovered_below, cfg.merge_threshold, cfg.diversity_target) == (0.5, 0.85, (3.0, 4.0))
    d = tmp_path / "configs" / "scenariogen"
    d.mkdir(parents=True)
    (d / "coverage.json").write_text(json.dumps({"tier": "mild", "varianse_share": 0.8}))
    with pytest.raises(ConfigError, match="varianse_share"):
        CoverageConfig.load(tmp_path)


def _result(name: str, tier: str, pnls: dict[str, float]) -> dict:
    return {"scenario": name, "severity": tier, "source": "static",
            "trades": [{"trade_id": t, "pnl": p} for t, p in pnls.items()]}


def test_scenario_matrix_takes_the_tier_and_lists_held_and_stopped():
    report = {"scenarios": [
        _result("a", "mild", {"T1": -1.0, "T2": -2.0, "T3": 0.5}),
        _result("a", "adverse", {"T1": -9.0, "T2": -9.0, "T3": 9.0}),
        _result("b", "adverse", {"T1": -3.0, "T2": 0.0, "T3": 0.0}),        # mild held by the gate
        _result("c", "mild", {"T2": 4.0, "T1": 1.0, "T3": -1.0}),           # different trade order
        {**_result("llm", "mild", {"T1": 7.0}), "source": "llm"},
    ], "stopped": [{"scenario": "d", "reason": "macro_unavailable"}]}
    names, trades, m, missing = scenario_matrix(report, "mild")
    assert names == ["a", "c"] and trades == TRADES
    assert m.tolist() == [[-1.0, -2.0, 0.5], [1.0, 4.0, -1.0]]
    assert [x["scenario"] for x in missing] == ["b", "d"]
    assert "held" in missing[0]["reason"] and "macro_unavailable" in missing[1]["reason"]


def test_build_report_flags_the_uncovered_direction_and_merges():
    cfg = CoverageConfig()
    # a and b both lie along U1 + U2 mix; c is pure U2 (pair). Nothing spans U3 (rates).
    scen = np.array([-np.sqrt(2.0) * (3 * U1 + U2), -np.sqrt(2.0) * (3 * U1 + 1.1 * U2), np.sqrt(2.0) * U2])
    rep = build_report(["a", "b", "c"], TRADES, scen, _history(), cfg)
    # variances 16, 4, 1 (x 8/7 for ddof 1): shares 16/21 and 20/21, so 0.9 keeps two directions
    assert rep["n_directions"] == 2
    d1, d2 = rep["directions"]
    assert d1["variance_share"] == pytest.approx(16 / 21) and d2["variance_share"] == pytest.approx(4 / 21)
    assert d1["spanned_share"] == pytest.approx(1.0) and d2["spanned_share"] == pytest.approx(1.0)
    assert rep["coverage"] == pytest.approx(1.0)
    assert not d1["uncovered"] and d1["book_exposure"] > 0
    assert {x["trade_id"] for x in d1["top_loadings"][:2]} == {"T1", "T2"}
    # With U1 removed, a, b and c all lie along +-U2: a and b are parallel (cos 1), c is opposite.
    assert rep["merge_pairs"] == [{"a": "a", "b": "b", "cosine": pytest.approx(1.0)}]
    cos = np.array(rep["cosine"]["matrix"])
    assert cos[0, 2] == pytest.approx(-1.0)
    # losses: a 6, b 6, c 0 (c gains nothing in total: +1 -1) -> only a, b kept, one direction -> PR 1
    assert rep["diversity"]["losses"] == {"a": pytest.approx(6.0), "b": pytest.approx(6.0), "c": pytest.approx(0.0)}
    assert rep["diversity"]["participation_ratio"] == pytest.approx(1.0)
    assert rep["diversity"]["in_target"] is False


def test_build_report_uncovered_rates_sleeve():
    cfg = CoverageConfig(variance_share=1.0)
    scen = np.array([-(U1 + U2), -(U1 - U2)])            # spans U1, U2 only
    rep = build_report(["x", "y"], TRADES, scen, _history(), cfg)
    assert rep["n_directions"] == 3
    d3 = rep["directions"][2]
    assert d3["spanned_share"] == pytest.approx(0.0, abs=1e-12) and d3["uncovered"]
    assert rep["coverage"] == pytest.approx(20 / 21)
    # x and y are orthogonal, but after removing U1 both lie along -+U2: one pattern, PR 1
    assert rep["diversity"]["participation_ratio"] == pytest.approx(1.0)


def test_runner_args():
    args = _parse_args(["--report", "r.json", "--tier", "adverse", "--workers", "3"])
    assert (args.report, args.tier, args.workers) == ("r.json", "adverse", 3)
    assert _parse_args([]).report is None
