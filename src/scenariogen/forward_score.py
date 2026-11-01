"""Forward-scoring runner: score matured scenario predictions against realised factor moves.

    python -m src.scenariogen.forward_score                 # score everything matured so far
    python -m src.scenariogen.forward_score --backfill      # first import past reports (pit_clean=false)
    python -m src.scenariogen.forward_score --as-of 2026-10-30

A prediction (``forward_log.PredictionLog``) matures once its market factor has ``horizon_days``
trading days after ``as_of``. Each factor's realised move comes from its own full series
(``FactorHistory.factor_series``). Scores go to ``forward_log.ScoreLog``; the summary goes to
``data/pricer/scenario/forward_scores_<date>.json``. Live (``pit_clean``) and backfilled records are
summarised separately, and every figure is split into all windows and stress windows.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import storage
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.runner import _setup_logging
from src.scenariogen.ablation import LAYERS
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.forward_log import (
    PREDICTION_KEY,
    PredictionLog,
    ScoreLog,
    horizon_scale,
    layer_value,
    live_triggers,
    prediction_record,
    realised_moves,
    score_prediction,
    stress_window,
)

logger = logging.getLogger("scenariogen.forward_score")

MARKET_FACTOR = "eq:XLK"
METRICS = ("cosine", "rank_corr_equity", "direction_hit_rate")


def _key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(row[k]) for k in PREDICTION_KEY)


def _rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [] if df.empty else df.to_dict(orient="records")


# --------------------------------------------------------------------------- #
# Backfill
# --------------------------------------------------------------------------- #
def backfill_rows(reports: Sequence[Mapping[str, Any]], covariance: FactorCovariance,
                  existing: set[tuple[str, ...]]) -> list[dict[str, Any]]:
    """Prediction rows from past stress and ablation reports, marked ``pit_clean = false``.

    Their service inputs can't be shown to be point-in-time, and the scale is recomputed from the
    current covariance (``scale_source = "recomputed"``). LLM-generated scenarios and ablation
    subsets without a stored vector are skipped, as is any key already in the log.
    """
    rows: list[dict[str, Any]] = []

    def add(row: dict[str, Any]) -> None:
        if _key(row) not in existing:
            existing.add(_key(row))
            rows.append(row)

    for rep in reports:
        # (scenario, severity, layers, horizon, vector, scenario_version, rule, triggers) per item
        items: list[tuple[str, str, list[str], int, dict[str, float], str, str, dict[str, Any]]] = []
        if rep.get("composition_rule") is None:                  # stress report (runner)
            for res in rep.get("scenarios", []):
                mat = res.get("materialized") or {}
                if res.get("source") == "llm" or not mat.get("vector"):
                    continue
                prov = mat.get("provenance") or {}
                items.append((
                    str(res["scenario"]), str(res["severity"]),
                    list(LAYERS) if res.get("source") == "dualrag" else [],
                    int(prov.get("horizon_days", 10)), mat["vector"],
                    str(prov.get("scenario_version", res.get("scenario_version", ""))), "backfill",
                    live_triggers(geopolitical=res.get("geopolitical"), throughput=res.get("throughput"))))
        else:                                                    # ablation report
            for rec in rep.get("scenarios", []):
                items.extend(
                    (str(rec["scenario"]), str(rec["severity"]), list(sub["layers"]),
                     int(sub.get("horizon_days", 10)), sub["vector"], str(sub.get("scenario_version", "")),
                     str(rep["composition_rule"]), {})
                    for sub in rec.get("subsets", []) if sub.get("vector"))
        for scenario, severity, layers, horizon, vector, version, rule, triggers in items:
            add(prediction_record(
                run_id=str(rep["run_id"]), scenario=scenario, severity=severity, layers=layers,
                as_of=str(rep["valuation_date"]), horizon_days=horizon, vector=vector,
                scale=horizon_scale(covariance, horizon, vector), scenario_version=version,
                cov_id=str((rep.get("covariance") or {}).get("cov_id", "")),
                snapshot_id=str(rep.get("snapshot_id", "")), composition_rule=rule, triggers=triggers,
                pit_clean=False, source="backfill", scale_source="recomputed",
                predicted_at=str(rep["generated_at"]) if rep.get("generated_at") else None))
    return rows


def _load_reports(root: Path) -> list[dict[str, Any]]:
    base = storage.join(storage.data_base(root), "pricer", "scenario")
    out: list[dict[str, Any]] = []
    for pattern in ("*/scenario_*.json", "*/ablation_*.json"):
        for loc in sorted(storage.glob(base, pattern)):
            try:
                out.append(json.loads(storage.read_text(str(loc))))
            except (OSError, ValueError) as exc:
                logger.warning("backfill: skipping unreadable report %s (%s)", loc, exc)
    return out


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def score_rows(predictions: Sequence[Mapping[str, Any]], scored: set[tuple[str, ...]],
               series: Mapping[str, pd.Series], *, market_factor: str = MARKET_FACTOR,
               as_of: date | None = None) -> list[dict[str, Any]]:
    """Score every unscored prediction whose market factor has matured.

    Args:
        predictions: Prediction-log rows (JSON-encoded ``vector``/``scale``/``layers``).
        scored: Keys already scored.
        series: Each factor's own daily-move series.
        market_factor: The factor whose maturity gates scoring and whose z flags stress windows.
        as_of: Score with data up to this date only (default: all data).

    Returns:
        Score-log rows for the newly matured predictions.
    """
    cut = {f: (s.loc[pd.to_datetime(s.index) <= pd.Timestamp(as_of)] if as_of else s)
           for f, s in series.items()}
    now = datetime.now(UTC).isoformat()
    out: list[dict[str, Any]] = []
    for p in predictions:
        if _key(p) in scored:
            continue
        pred_as_of = date.fromisoformat(str(p["as_of"]))
        horizon = int(p["horizon_days"])
        if market_factor not in cut or realised_moves(
                cut[market_factor].to_frame(market_factor), pred_as_of, horizon, [market_factor]) is None:
            continue                                             # not matured yet
        vector: dict[str, float] = json.loads(p["vector"])
        scale: dict[str, float] = json.loads(p["scale"])
        realised: dict[str, float] = {}
        for f in vector:
            if f in cut:
                r = realised_moves(cut[f].to_frame(f), pred_as_of, horizon, [f])
                if r:
                    realised.update(r)
        s = score_prediction(vector, realised, scale)
        stress = stress_window(realised, scale, market_factor=market_factor)
        mz = (realised[market_factor] / scale[market_factor]
              if market_factor in realised and scale.get(market_factor, 0) > 0 else None)
        out.append({k: p[k] for k in (*PREDICTION_KEY, "as_of", "horizon_days", "pit_clean", "source")}
                   | s | {"stress": stress, "market_z": mz,
                          "realised": json.dumps(realised, sort_keys=True), "logged_at": now})
    return out


def summarise(scores: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-variant metric means and per-layer paired values, all windows vs stress windows."""
    def block(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        stress = [r for r in rows if r.get("stress") is True]
        variants: dict[str, dict[str, Any]] = {}
        for r in rows:
            label = f"{r['scenario']}|{r['severity']}|{','.join(r['layers']) or 'authored'}"
            variants.setdefault(label, {"rows": []})["rows"].append(r)
        out_variants: dict[str, Any] = {}
        for label, v in sorted(variants.items()):
            vr = v["rows"]
            vs = [r for r in vr if r.get("stress") is True]
            out_variants[label] = {
                "n": len(vr), "n_windows": len({r["as_of"] for r in vr}), "n_stress": len(vs),
                "mean_all": {m: _mean(vr, m) for m in METRICS},
                "mean_stress": {m: _mean(vs, m) for m in METRICS}}
        layers = {layer: {"all": {m: layer_value(rows, layer, m) for m in METRICS},
                          "stress": {m: layer_value(stress, layer, m) for m in METRICS}}
                  for layer in LAYERS}
        return {"n_scores": len(rows), "n_windows": len({r["as_of"] for r in rows}),
                "n_stress": len(stress), "variants": out_variants, "layers": layers}

    decoded = [dict(r) | {"layers": json.loads(r["layers"]) if isinstance(r["layers"], str) else list(r["layers"])}
               for r in scores]
    return {"live": block([r for r in decoded if r.get("pit_clean")]),
            "backfill": block([r for r in decoded if not r.get("pit_clean")])}


def _mean(rows: Sequence[Mapping[str, Any]], metric: str) -> float | None:
    vals = [float(r[metric]) for r in rows if r.get(metric) is not None and not pd.isna(r[metric])]
    return sum(vals) / len(vals) if vals else None


def run_forward_score(*, backfill: bool = False, as_of: date | None = None,
                      market_factor: str = MARKET_FACTOR) -> dict[str, Any]:
    """Backfill (optionally), score the matured predictions and write the summary report."""
    root = get_project_root()
    config = load_pricer_config()
    history = FactorHistory(root, config)
    plog, slog = PredictionLog(root), ScoreLog(root)
    batch = f"score-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"

    if backfill:
        cov = ScenarioCatalog.from_config(history, project_root=root).covariance
        existing = {_key(r) for r in _rows(plog.load())}
        rows = backfill_rows(_load_reports(root), cov, existing)
        plog.write(rows, f"backfill-{batch}")
        logger.info("Backfilled %d prediction(s) from past reports (pit_clean=false)", len(rows))

    predictions = _rows(plog.load())
    scored = {_key(r) for r in _rows(slog.load())}
    new = score_rows(predictions, scored, history.factor_series(include_style=True),
                     market_factor=market_factor, as_of=as_of)
    slog.write(new, batch)
    logger.info("Scored %d newly matured prediction(s) of %d logged", len(new), len(predictions))

    report = {"generated_at": datetime.now(UTC).isoformat(),
              "as_of": (as_of or datetime.now(UTC).date()).isoformat(), "market_factor": market_factor,
              "note": "Shape scores of stress scenarios against realised moves; read stress windows "
                      "apart from calm ones. Overlapping windows are not independent evidence.",
              "n_predictions": len(predictions), "n_newly_scored": len(new),
              **summarise(_rows(slog.load()))}
    out = storage.join(storage.data_base(root), "pricer", "scenario",
                       f"forward_scores_{report['as_of']}.json")
    storage.write_text_atomic(out, json.dumps(report, indent=2, default=str))
    logger.info("Wrote forward-score report -> %s", out)
    return report


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Score matured scenario predictions (forward scoring log)")
    p.add_argument("--backfill", action="store_true",
                   help="first import past scenario/ablation reports (pit_clean=false)")
    p.add_argument("--as-of", default=None, help="score with data up to this ISO date only")
    p.add_argument("--market-factor", default=MARKET_FACTOR, help="stress-window market factor")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _setup_logging()
    args = _parse_args(argv)
    run_forward_score(backfill=args.backfill,
                      as_of=date.fromisoformat(args.as_of) if args.as_of else None,
                      market_factor=args.market_factor)
    return 0


if __name__ == "__main__":
    sys.exit(main())
