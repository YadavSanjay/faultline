"""Forward scoring log: what each run predicted, scored later against what markets did.

A stress scenario has no observed answer when it is made, so its value can only be judged forward:
keep the materialized factor vector of every run (and of every ablation layer subset), then, once
its horizon has passed, compare it with the realised factor moves. A stress scenario is not a
forecast, so the scores measure **shape**: cross-sectional rank, direction on factors that really
moved, and the cosine of volatility-scaled vectors. Stress windows are flagged so they can be read
apart from calm ones, and a layer's value is a paired with-minus-without comparison within one run.

Records live in two append-only Parquet datasets under the storage façade
(``data/pricer/store/scenario_predictions`` and ``.../scenario_scores``); a re-logged key is an
upsert, resolved at read time by the latest ``logged_at``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa

from src.common import storage

from .covariance import FactorCovariance

PREDICTION_KEY = ("run_id", "scenario", "severity", "layers")
# Upsert-on-read: the latest logged_at per PREDICTION_KEY wins.
_LATEST_SQL = ("SELECT * EXCLUDE (rn) FROM (SELECT *, row_number() OVER (PARTITION BY run_id, scenario, "
               "severity, layers ORDER BY logged_at DESC) AS rn FROM @SRC) WHERE rn = 1")

_PRED_SCHEMA = pa.schema([
    ("run_id", pa.string()), ("scenario", pa.string()), ("severity", pa.string()),
    ("layers", pa.string()), ("as_of", pa.string()), ("horizon_days", pa.int64()),
    ("vector", pa.string()), ("scale", pa.string()), ("scale_source", pa.string()),
    ("scenario_version", pa.string()), ("cov_id", pa.string()), ("snapshot_id", pa.string()),
    ("composition_rule", pa.string()), ("triggers", pa.string()), ("pit_clean", pa.bool_()),
    ("source", pa.string()), ("predicted_at", pa.string()), ("logged_at", pa.string()),
])

# A prediction counts as point-in-time clean only if it was made within this many calendar days of
# its valuation date: a later run sees part of its own scoring window (news, MacroRAG corpus).
MAX_PIT_LAG_DAYS = 3

_SCORE_SCHEMA = pa.schema([
    ("run_id", pa.string()), ("scenario", pa.string()), ("severity", pa.string()),
    ("layers", pa.string()), ("as_of", pa.string()), ("horizon_days", pa.int64()),
    ("n_factors", pa.int64()), ("cosine", pa.float64()), ("rank_corr_equity", pa.float64()),
    ("direction_hit_rate", pa.float64()), ("n_direction", pa.int64()), ("stress", pa.bool_()),
    ("market_z", pa.float64()), ("realised", pa.string()), ("pit_clean", pa.bool_()),
    ("source", pa.string()), ("logged_at", pa.string()),
])


# --------------------------------------------------------------------------- #
# Scoring (locked by spec scenario-forward-log)
# --------------------------------------------------------------------------- #
def _compounds(factor: str) -> bool:
    return factor.startswith(("eq:", "fx:"))


def realised_moves(
    panel: pd.DataFrame,
    as_of: date,
    horizon_days: int,
    factors: Sequence[str],
) -> dict[str, float] | None:
    """Returns each requested factor's realised move over the rows strictly after ``as_of``.

    Args:
        panel: Daily factor moves indexed by date (ascending), canonical factor keys as columns;
            ``eq:``/``fx:`` columns are simple returns, every other column a daily change.
        as_of: The prediction's valuation date; its own row is excluded.
        horizon_days: Number of panel rows after ``as_of`` that make up the window.
        factors: Factor keys to return.

    Returns:
        Factor key -> realised move (compounded for ``eq:``/``fx:``, summed otherwise), leaving out
        factors absent from ``panel`` or with a missing value in the window; ``None`` when fewer than
        ``horizon_days`` rows exist after ``as_of``.
    """
    index = pd.to_datetime(panel.index)
    window = panel.loc[index > pd.Timestamp(as_of)].iloc[:horizon_days]
    if len(window) < horizon_days:
        return None
    out: dict[str, float] = {}
    for factor in factors:
        if factor not in window.columns:
            continue
        col = pd.to_numeric(window[factor], errors="coerce")
        if col.isna().any():
            continue
        values = col.to_numpy(dtype=float)
        out[factor] = float(np.prod(1.0 + values) - 1.0) if _compounds(factor) else float(values.sum())
    return out


def _spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 3:
        return None
    ra = pd.Series(a).rank(method="average").to_numpy(dtype=float)
    rb = pd.Series(b).rank(method="average").to_numpy(dtype=float)
    ra -= ra.mean()
    rb -= rb.mean()
    den = math.sqrt(float(np.dot(ra, ra)) * float(np.dot(rb, rb)))
    return None if den == 0.0 else float(np.dot(ra, rb) / den)


def score_prediction(
    predicted: Mapping[str, float],
    realised: Mapping[str, float],
    scale: Mapping[str, float],
    *,
    min_abs_z: float = 1.0,
) -> dict[str, Any]:
    """Scores a predicted factor vector's shape against the realised move.

    Args:
        predicted: Factor key -> predicted move.
        realised: Factor key -> realised move.
        scale: Factor key -> horizon volatility; only factors with ``scale > 0`` are scored.
        min_abs_z: Minimum ``|z_realised|`` for a factor to count towards the direction hit rate.

    Returns:
        A dict with ``n_factors``, ``cosine``, ``rank_corr_equity``, ``direction_hit_rate`` and
        ``n_direction``.
    """
    common = [f for f in predicted if f in realised and f in scale and scale[f] > 0]
    zp = np.array([predicted[f] / scale[f] for f in common], dtype=float)
    zr = np.array([realised[f] / scale[f] for f in common], dtype=float)

    cosine: float | None = None
    if len(common) >= 2:
        norm = float(np.linalg.norm(zp)) * float(np.linalg.norm(zr))
        if norm > 0.0:
            cosine = float(np.dot(zp, zr) / norm)

    eq_idx = [i for i, f in enumerate(common) if f.startswith("eq:")]
    rank = _spearman([float(zp[i]) for i in eq_idx], [float(zr[i]) for i in eq_idx])

    qualifying = [i for i in range(len(common)) if abs(zr[i]) >= min_abs_z]
    hits = sum(1 for i in qualifying if zp[i] != 0.0 and np.sign(zp[i]) == np.sign(zr[i]))
    hit_rate = hits / len(qualifying) if qualifying else None
    return {"n_factors": len(common), "cosine": cosine, "rank_corr_equity": rank,
            "direction_hit_rate": hit_rate, "n_direction": len(qualifying)}


def stress_window(
    realised: Mapping[str, float],
    scale: Mapping[str, float],
    *,
    market_factor: str = "eq:XLK",
    threshold_z: float = -2.0,
) -> bool | None:
    """Says whether the realised window was a stress for the book's market factor.

    Args:
        realised: Factor key -> realised move.
        scale: Factor key -> horizon volatility.
        market_factor: The book's market factor key.
        threshold_z: The z at or below which the window counts as a stress.

    Returns:
        ``True`` when the market factor's z is at or below ``threshold_z``, ``False`` otherwise, and
        ``None`` when the factor or its scale is missing or the scale is not positive.
    """
    if market_factor not in realised or market_factor not in scale:
        return None
    s = scale[market_factor]
    if not s > 0:
        return None
    return bool(realised[market_factor] / s <= threshold_z)


def _record_key(r: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(r["run_id"]), str(r["scenario"]), str(r["severity"]))


def layer_value(records: Sequence[Mapping[str, Any]], layer: str, metric: str) -> dict[str, Any]:
    """Measures a layer's value as paired with-minus-without deltas of a score metric.

    Args:
        records: Score records carrying ``run_id``, ``scenario``, ``severity``, ``layers`` and the
            metric keys.
        layer: The layer id to evaluate.
        metric: The score metric key to compare.

    Returns:
        A dict with ``layer``, ``metric``, ``n_pairs``, ``mean_delta``, ``n_improved`` and ``n_worse``.
    """
    by_key: dict[tuple[tuple[str, str, str], frozenset[str]], Mapping[str, Any]] = {}
    for r in records:
        by_key[(_record_key(r), frozenset(r["layers"]))] = r
    deltas: list[float] = []
    for (key, layers), with_rec in by_key.items():
        if layer not in layers:
            continue
        without_rec = by_key.get((key, layers - {layer}))
        if without_rec is None:
            continue
        a, b = with_rec.get(metric), without_rec.get(metric)
        if a is None or b is None:
            continue
        deltas.append(float(a) - float(b))
    return {"layer": layer, "metric": metric, "n_pairs": len(deltas),
            "mean_delta": float(np.mean(deltas)) if deltas else None,
            "n_improved": sum(1 for d in deltas if d > 0), "n_worse": sum(1 for d in deltas if d < 0)}


# --------------------------------------------------------------------------- #
# Records and stores
# --------------------------------------------------------------------------- #
def horizon_scale(cov: FactorCovariance, horizon_days: int, factors: Iterable[str]) -> dict[str, float]:
    """Per-factor horizon volatility ``sqrt(Σ_ii · horizon_days)`` from a daily covariance."""
    out: dict[str, float] = {}
    for f in factors:
        if f in cov.factors:
            var = float(cov.cov[cov.index(f), cov.index(f)])
            if var > 0.0 and math.isfinite(var):
                out[f] = math.sqrt(var * horizon_days)
    return out


def live_triggers(*, geopolitical: Mapping[str, Any] | None = None,
                  throughput: Mapping[str, Any] | None = None,
                  news: Any = None, crowding: Any = None,
                  event_hits: Sequence[Any] | None = None) -> dict[str, Any]:
    """The live triggers that fired for a scenario run, for the prediction record.

    ``geopolitical``/``throughput`` are the resolutions' ``to_dict()`` (their ``trigger`` flag);
    ``news`` is a ``NewsResolution`` (8-K item -> tickers) and ``crowding`` a ``CrowdingResolution``
    (crowded-short squeeze names). ``event_hits`` are the scenario's own 8-K trigger hits
    (``events.TriggerHit``, from ``events.json``); when given they replace the theme-agnostic
    ``news`` items under ``news_items``. Absent inputs contribute nothing.
    """
    out: dict[str, Any] = {}
    if geopolitical is not None:
        out["geopolitical"] = bool(geopolitical.get("trigger"))
    if throughput is not None:
        out["throughput"] = bool(throughput.get("trigger"))
    if event_hits is not None:
        out["news_items"] = [dict(h._asdict()) for h in event_hits]
    elif news is not None and getattr(news, "applied", False):
        out["news_items"] = dict(getattr(news, "live_triggers", {}) or {})
    if crowding is not None and getattr(crowding, "applied", False):
        out["crowding_squeeze"] = list(getattr(crowding, "squeeze_names", []) or [])
    return out


def _now() -> str:
    return datetime.now(UTC).isoformat()


def is_pit_clean(as_of: str, predicted_at: datetime, *, max_lag_days: int = MAX_PIT_LAG_DAYS) -> bool:
    """Whether a prediction made at ``predicted_at`` for valuation date ``as_of`` is forward-only.

    A run made more than ``max_lag_days`` after its valuation date has inputs (news, the MacroRAG
    corpus) that overlap its own scoring window, so its score would carry look-ahead.
    """
    lag = (predicted_at.date() - date.fromisoformat(as_of)).days
    return 0 <= lag <= max_lag_days


def prediction_record(
    *,
    run_id: str,
    scenario: str,
    severity: str,
    layers: Iterable[str],
    as_of: str,
    horizon_days: int,
    vector: Mapping[str, float],
    scale: Mapping[str, float],
    scenario_version: str,
    cov_id: str,
    snapshot_id: str,
    composition_rule: str,
    triggers: Mapping[str, Any],
    pit_clean: bool,
    source: str,
    scale_source: str = "prediction",
    predicted_at: str | None = None,
) -> dict[str, Any]:
    """One row of the prediction log (JSON-encoded maps, sorted layer list).

    ``pit_clean`` is the caller's claim; a live runner passes ``is_pit_clean(as_of, now)``.
    ``predicted_at`` defaults to now (for a backfill, pass the original report's time).
    """
    return {
        "run_id": run_id, "scenario": scenario, "severity": severity,
        "layers": json.dumps(sorted(layers)), "as_of": as_of, "horizon_days": int(horizon_days),
        "vector": json.dumps({k: float(v) for k, v in sorted(vector.items())}),
        "scale": json.dumps({k: float(v) for k, v in sorted(scale.items())}),
        "scale_source": scale_source, "scenario_version": scenario_version, "cov_id": cov_id,
        "snapshot_id": snapshot_id, "composition_rule": composition_rule,
        "triggers": json.dumps(dict(triggers), default=str, sort_keys=True),
        "pit_clean": bool(pit_clean), "source": source, "predicted_at": predicted_at or _now(),
        "logged_at": _now(),
    }


class _KeyedLog:
    """An append-only keyed Parquet dataset; the latest ``logged_at`` per key wins on read."""

    def __init__(self, project_root: Path, name: str, schema: pa.Schema):
        self.dataset_uri = storage.join(storage.data_base(project_root), "pricer", "store", name)
        self.schema = schema

    def write(self, rows: list[dict[str, Any]], batch_id: str) -> str | None:
        if not rows:
            return None
        storage.write_dataset(pa.Table.from_pylist(rows, schema=self.schema), self.dataset_uri,
                              partition_cols=["as_of"], basename_template=f"{batch_id}-{{i}}.parquet")
        return self.dataset_uri

    def load(self) -> pd.DataFrame:
        """The latest row per ``PREDICTION_KEY`` (empty frame when nothing is logged yet)."""
        return storage.query(_LATEST_SQL, self.dataset_uri)


class PredictionLog(_KeyedLog):
    """The prediction log (``data/pricer/store/scenario_predictions``)."""

    def __init__(self, project_root: Path):
        super().__init__(project_root, "scenario_predictions", _PRED_SCHEMA)


class ScoreLog(_KeyedLog):
    """Scores of matured predictions (``data/pricer/store/scenario_scores``), keyed like predictions."""

    def __init__(self, project_root: Path):
        super().__init__(project_root, "scenario_scores", _SCORE_SCHEMA)
