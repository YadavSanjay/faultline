"""Unit tests for the forward scoring log's store, writers, backfill and scoring runner.

The locked spec tests cover the scoring functions; these cover the wiring around them, with
synthetic series and reports (no data/, no services, no pricing).
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest
from src.scenariogen.ablation import COMPOSITION_RULE, LAYERS
from src.scenariogen.ablation_runner import prediction_rows
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.forward_log import (
    PredictionLog,
    ScoreLog,
    horizon_scale,
    live_triggers,
    prediction_record,
)
from src.scenariogen.forward_score import backfill_rows, score_rows, summarise


def _cov() -> FactorCovariance:
    factors = ("eq:XLK", "eq:NVDA", "credit")
    var = np.array([0.0001, 0.0004, 1e-8])
    return FactorCovariance(factors=factors, mean=np.zeros(3), cov=np.diag(var), n_obs=250,
                            window="test", cov_id="cov-test")


def _record(run_id: str = "r1", layers: list[str] | None = None, as_of: str = "2026-01-05",
            vector: dict[str, float] | None = None, pit_clean: bool = True) -> dict:
    vec = vector or {"eq:XLK": -0.10, "eq:NVDA": -0.20, "credit": 0.002}
    return prediction_record(
        run_id=run_id, scenario="ai_bust", severity="mild", layers=layers or [], as_of=as_of,
        horizon_days=2, vector=vec, scale=horizon_scale(_cov(), 2, vec), scenario_version="v1",
        cov_id="cov-test", snapshot_id="snap", composition_rule=COMPOSITION_RULE, triggers={},
        pit_clean=pit_clean, source="stress")


def test_horizon_scale_is_daily_vol_times_sqrt_horizon():
    s = horizon_scale(_cov(), 4, ["eq:XLK", "eq:NVDA", "eq:MISSING"])
    assert s == pytest.approx({"eq:XLK": 0.01 * 2.0, "eq:NVDA": 0.02 * 2.0}, rel=1e-12)  # exact algebra


def test_pit_clean_only_within_the_lag_of_the_valuation_date():
    from datetime import UTC, datetime

    from src.scenariogen.forward_log import is_pit_clean

    assert is_pit_clean("2026-10-06", datetime(2026, 10, 6, 22, tzinfo=UTC))
    assert is_pit_clean("2026-10-06", datetime(2026, 10, 9, 8, tzinfo=UTC))       # 3 days: weekend
    assert not is_pit_clean("2026-10-06", datetime(2026, 10, 15, 8, tzinfo=UTC))  # sees its window
    assert not is_pit_clean("2026-10-06", datetime(2026, 10, 5, 8, tzinfo=UTC))   # before as_of


def test_live_triggers_collects_only_what_fired_or_applies():
    class News:
        applied = True
        live_triggers = {"5.02": ["NVDA"]}

    class Crowding:
        applied = True
        squeeze_names = ["SMH"]

    t = live_triggers(geopolitical={"trigger": True}, throughput={"trigger": False}, news=News(),
                      crowding=Crowding())
    assert t == {"geopolitical": True, "throughput": False, "news_items": {"5.02": ["NVDA"]},
                 "crowding_squeeze": ["SMH"]}
    assert live_triggers() == {}


def test_prediction_log_round_trip_and_upsert(tmp_path):
    log = PredictionLog(tmp_path)
    assert log.load().empty
    log.write([_record(), _record(layers=["macrorag"])], "batch-1")
    again = _record() | {"source": "rerun", "logged_at": "9999-01-01T00:00:00+00:00"}
    log.write([again], "batch-2")
    df = log.load()
    assert len(df) == 2                                    # same key re-logged = upsert
    base = df[df["layers"] == "[]"].iloc[0]
    assert base["source"] == "rerun"
    assert json.loads(base["vector"])["eq:NVDA"] == pytest.approx(-0.20, rel=1e-12)


def _series(n: int = 6) -> dict[str, pd.Series]:
    idx = pd.date_range("2026-01-05", periods=n, freq="B")
    return {"eq:XLK": pd.Series([0.0, -0.05, -0.05, 0.01, 0.0, 0.0], index=idx[:6][:n]),
            "eq:NVDA": pd.Series([0.0, -0.10, -0.10, 0.0, 0.0, 0.0], index=idx[:6][:n]),
            "credit": pd.Series([0.0, 0.001, 0.001, 0.0, 0.0, 0.0], index=idx[:6][:n])}


def test_score_rows_scores_only_matured_and_unscored():
    rows = [_record("r1", as_of="2026-01-05"), _record("r2", as_of="2026-01-12")]  # r2 not matured
    scored = score_rows(rows, set(), _series())
    assert [r["run_id"] for r in scored] == ["r1"]
    r = scored[0]
    realised = json.loads(r["realised"])
    assert realised["eq:XLK"] == pytest.approx(0.95 * 0.95 - 1.0, rel=1e-12)   # compounded, 2 days
    assert realised["credit"] == pytest.approx(0.002, rel=1e-12)              # summed
    # XLK realised -9.75% over a 2-day scale of 1% * sqrt(2): far below -2 sigma -> stress window.
    assert r["stress"] is True
    assert r["market_z"] == pytest.approx((0.95 * 0.95 - 1.0) / (0.01 * math.sqrt(2)), rel=1e-12)
    assert r["direction_hit_rate"] == pytest.approx(1.0)
    key = ("r1", "ai_bust", "mild", "[]")
    assert score_rows(rows, {key}, _series()) == []                           # already scored


def test_score_rows_respects_as_of_cut():
    rows = [_record("r1", as_of="2026-01-05")]
    assert score_rows(rows, set(), _series(), as_of=pd.Timestamp("2026-01-06").date()) == []


def test_backfill_from_stress_and_ablation_reports():
    stress = {"valuation_date": "2026-01-05", "run_id": "s1", "snapshot_id": "snap",
              "covariance": {"cov_id": "c"}, "scenarios": [
                  {"scenario": "taiwan", "severity": "mild", "source": "dualrag",
                   "materialized": {"vector": {"eq:XLK": -0.1}, "provenance": {"horizon_days": 10,
                                                                             "scenario_version": "v9"}},
                   "geopolitical": {"trigger": True}},
                  {"scenario": "llm_x", "severity": "mild", "source": "llm",
                   "materialized": {"vector": {"eq:XLK": -0.2}, "provenance": {}}}]}
    ablation = {"valuation_date": "2026-01-05", "run_id": "a1", "composition_rule": "dualrag-v1",
                "covariance": {"cov_id": "c"}, "scenarios": [
                    {"scenario": "ai_bust", "severity": "mild", "subsets": [
                        {"layers": [], "vector": {"eq:NVDA": -0.3}, "horizon_days": 10},
                        {"layers": ["macrorag"], "horizon_days": 10}]}]}   # no vector: old report
    existing: set = set()
    rows = backfill_rows([stress, ablation], _cov(), existing)
    assert [(r["run_id"], r["scenario"], r["layers"]) for r in rows] == [
        ("s1", "taiwan", json.dumps(sorted(LAYERS))), ("a1", "ai_bust", "[]")]
    assert all(r["pit_clean"] is False and r["scale_source"] == "recomputed" for r in rows)
    assert json.loads(rows[0]["triggers"]) == {"geopolitical": True}
    assert backfill_rows([stress, ablation], _cov(), existing) == []            # keys already logged


def test_ablation_prediction_rows_log_every_subset():
    rec = {"scenario": "ai_bust", "severity": "mild", "subsets": [
        {"layers": [], "vector": {"eq:XLK": -0.1}, "horizon_days": 10, "scenario_version": "v"},
        {"layers": ["news"], "vector": {"eq:XLK": -0.1}, "horizon_days": 10, "scenario_version": "v"}]}
    from datetime import UTC, datetime

    today = datetime.now(UTC).date().isoformat()
    rows = prediction_rows(rec, run_id="r", as_of=today, covariance=_cov(), snapshot_id="s",
                           triggers={"geopolitical": False})
    assert [r["layers"] for r in rows] == ["[]", '["news"]']
    assert all(r["source"] == "ablation" and r["pit_clean"] is True for r in rows)
    late = prediction_rows(rec, run_id="r", as_of="2026-01-05", covariance=_cov(), snapshot_id="s",
                           triggers={})
    assert all(r["pit_clean"] is False for r in late)           # run long after its valuation date


def test_summarise_splits_live_and_backfill_and_pairs_layers(tmp_path):
    base = {"run_id": "r1", "scenario": "ai_bust", "severity": "mild", "as_of": "2026-01-05",
            "stress": True, "cosine": 0.5, "rank_corr_equity": None, "direction_hit_rate": 0.5}
    scores = [base | {"layers": "[]", "pit_clean": True},
              base | {"layers": '["news"]', "pit_clean": True, "cosine": 0.7},
              base | {"layers": "[]", "pit_clean": False, "run_id": "old"}]
    out = summarise(scores)
    assert out["live"]["n_scores"] == 2 and out["backfill"]["n_scores"] == 1
    news = out["live"]["layers"]["news"]["stress"]["cosine"]
    assert news["n_pairs"] == 1
    assert news["mean_delta"] == pytest.approx(0.2, rel=1e-12)
    ScoreLog(tmp_path)  # constructs without touching data/
