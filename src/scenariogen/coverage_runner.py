"""Scenario-set coverage runner: does the set span the ways this book loses money? (design §7.3, G3).

Reads a stress report, takes each scenario's per-trade P&L at one tier (the scenario matrix), reprices
the same trades over the full-revaluation VaR history (the history matrix) and reports, with the
measures in ``coverage.py``:

* **coverage**: the variance-weighted share of the book's principal loss directions spanned by the
  scenarios, with each direction's spanned share and largest trade loadings (uncovered below
  ``uncovered_below``);
* **diversity**: the loss-weighted participation ratio of the scenarios once the book's first direction
  is removed, against the target band;
* the beta-removed cosine matrix and the merge pairs above ``merge_threshold``.

Scenarios without a result at the tier (held by the gate, stopped) are listed and left out.

    python -m src.scenariogen.coverage_runner                        # latest stress report, mild
    python -m src.scenariogen.coverage_runner --report data/pricer/scenario/2026-10-06/scenario_<id>.json

Writes ``data/pricer/scenario/<date>/coverage_<stress run id>.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from src.common import storage
from src.common.config.models import ConfigError
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.risk.historical_var import FullRevalVarEngine
from src.pricer.runner import _build_ctx, _setup_logging
from src.scenariogen.config import config_dir
from src.scenariogen.coverage import (
    merge_pairs,
    participation_ratio,
    principal_directions,
    remove_direction,
    set_coverage,
    spanned_share,
)
from src.scenariogen.report import _atomic_write

logger = logging.getLogger("scenariogen.coverage")

N_LOADINGS = 5


@dataclass(frozen=True)
class CoverageConfig:
    """``configs/scenariogen/coverage.json`` (a mistyped key raises ``ConfigError``)."""

    tier: str = "mild"
    variance_share: float = 0.9
    max_components: int = 10
    uncovered_below: float = 0.5
    merge_threshold: float = 0.85
    diversity_target: tuple[float, float] = (3.0, 4.0)

    @classmethod
    def load(cls, root: Path | None = None) -> CoverageConfig:
        raw = json.loads((config_dir(root) / "coverage.json").read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if not k.startswith(("_", "description"))}
        unknown = set(values) - known
        if unknown:
            raise ConfigError(f"coverage.json: unknown key(s) {sorted(unknown)}; expected {sorted(known)}")
        if "diversity_target" in values:
            lo, hi = (float(x) for x in values["diversity_target"])
            values["diversity_target"] = (lo, hi)
        return cls(**values)


def scenario_matrix(report: Mapping[str, Any], tier: str,
                    ) -> tuple[list[str], list[str], np.ndarray, list[dict[str, str]]]:
    """Per-trade P&L of each scenario at ``tier`` from a stress report.

    Returns:
        ``(names, trade_ids, matrix, missing)``: scenario names in report order, the trade ids (first
        result's order), the scenarios x trades P&L matrix (a trade absent from a result counts 0), and
        the scenarios that ran some tier but have no result at ``tier`` (held by the gate) or stopped.
    """
    results = [r for r in report.get("scenarios", []) if r.get("source") != "llm"]
    at_tier = [r for r in results if r.get("severity") == tier]
    names = [str(r["scenario"]) for r in at_tier]
    trade_ids = [str(t["trade_id"]) for t in at_tier[0]["trades"]] if at_tier else []
    matrix = np.zeros((len(at_tier), len(trade_ids)))
    col = {tid: j for j, tid in enumerate(trade_ids)}
    for i, r in enumerate(at_tier):
        for t in r["trades"]:
            if str(t["trade_id"]) in col:
                matrix[i, col[str(t["trade_id"])]] = float(t["pnl"])
    missing = [{"scenario": n, "reason": f"no {tier} result (held by the gate)"}
               for n in dict.fromkeys(str(r["scenario"]) for r in results) if n not in names]
    missing += [{"scenario": str(s.get("scenario")), "reason": f"stopped: {s.get('reason', '')}"}
                for s in report.get("stopped", [])]
    return names, trade_ids, matrix, missing


def build_report(names: Sequence[str], trade_ids: Sequence[str], scenarios: np.ndarray, history: np.ndarray,
                 cfg: CoverageConfig) -> dict[str, Any]:
    """The coverage, diversity and merge measures for one scenario set (no I/O).

    Args:
        names: Scenario names, one per row of ``scenarios``.
        trade_ids: Trade ids, one per column of both matrices.
        scenarios: Scenarios x trades per-trade scenario P&L.
        history: Days x trades per-trade historical P&L.
        cfg: Report parameters.

    Returns:
        The report body: ``coverage``, ``directions``, ``diversity``, ``cosine`` and ``merge_pairs``.
    """
    directions, variances = principal_directions(history, variance_share=cfg.variance_share,
                                                 max_components=cfg.max_components)
    shares = spanned_share(directions, scenarios)
    total_var = float(np.var(history - history.mean(axis=0), axis=0, ddof=1).sum())
    out_dirs = []
    for i, (v, lam, s) in enumerate(zip(directions, variances, shares, strict=True)):
        top = np.argsort(-np.abs(v))[:N_LOADINGS]
        out_dirs.append({
            "index": i + 1, "variance": float(lam), "variance_share": float(lam) / total_var,
            "spanned_share": float(s), "uncovered": bool(s < cfg.uncovered_below),
            # sum of the loadings > 0: the book gains moving along +direction (sign rule of coverage.py)
            "book_exposure": float(v.sum()),
            "top_loadings": [{"trade_id": trade_ids[j], "loading": float(v[j])} for j in top],
        })
    residual = remove_direction(scenarios, directions[0]) if len(names) else scenarios
    losses = -scenarios.sum(axis=1)
    norms = np.linalg.norm(residual, axis=1)
    safe = np.where(norms > 0.0, norms, 1.0)
    unit = residual / safe[:, None]
    cos = unit @ unit.T
    pr = participation_ratio(residual, losses) if len(names) else 0.0
    lo, hi = cfg.diversity_target
    return {
        "coverage": set_coverage(variances, shares),
        "n_directions": len(variances),
        "directions_variance_share": float(variances.sum()) / total_var,
        "directions": out_dirs,
        "diversity": {"participation_ratio": pr, "target": [lo, hi], "in_target": bool(lo <= pr <= hi),
                      "losses": {n: float(x) for n, x in zip(names, losses, strict=True)}},
        "cosine": {"names": list(names), "matrix": [[float(x) for x in row] for row in cos]},
        "merge_pairs": [{"a": a, "b": b, "cosine": c}
                        for a, b, c in merge_pairs(list(names), residual, threshold=cfg.merge_threshold)],
    }


def _latest_report(root: Path) -> str:
    base = storage.join(storage.data_base(root), "pricer", "scenario")
    locs = storage.glob(base, "*/scenario_*.json")
    stress = [str(p) for p in locs if "scenario_llm-" not in str(p)]
    if not stress:
        raise FileNotFoundError(f"no stress report under {base}")
    return max(stress, key=lambda p: json.loads(storage.read_text(p)).get("generated_at", ""))


def run_coverage(report_path: str | None = None, *, tier: str | None = None,
                 workers: int | None = None) -> dict[str, Any]:
    """Builds and writes the coverage report for one stress report.

    Args:
        report_path: Stress report to read; default the latest by ``generated_at``.
        tier: Severity tier; default the config's.
        workers: Processes for the full-reval history (default 1).

    Returns:
        The report payload (also written to ``data/pricer/scenario/<date>/coverage_<run_id>.json``).
    """
    root = get_project_root()
    cfg = CoverageConfig.load(root)
    tier = tier or cfg.tier
    path = report_path or _latest_report(root)
    report = json.loads(storage.read_text(path))
    names, trade_ids, scen, missing = scenario_matrix(report, tier)
    if not names:
        raise ValueError(f"{path}: no scenario results at tier {tier!r}")

    config = load_pricer_config()
    vdate = date.fromisoformat(str(report["valuation_date"]))
    ctx = _build_ctx(config, vdate, "PV")
    by_id = {t.trade_id: t for t in load_portfolio()}
    unknown = [t for t in trade_ids if t not in by_id]
    if unknown:
        raise ValueError(f"stress report trades not in the current portfolio: {unknown}")
    var_engine = FullRevalVarEngine(PricingEngine(config, root), config, root)
    hist = var_engine.trade_pnl_history([by_id[t] for t in trade_ids], ctx, workers=int(workers or 1))
    keep = [trade_ids.index(t) for t in hist.trade_ids]     # trades that priced in history
    dropped = [t for t in trade_ids if t not in hist.trade_ids]
    body = build_report(names, hist.trade_ids, scen[:, keep], hist.matrix, cfg)

    payload = {
        "valuation_date": report["valuation_date"],
        "generated_at": datetime.now(UTC).isoformat(),
        "stress_run_id": report.get("run_id"),
        "snapshot_id": report.get("snapshot_id"),
        "cov_id": (report.get("covariance") or {}).get("cov_id"),
        "tier": tier,
        "lookback_days": hist.lookback_days,
        "history_days": len(hist.dates),
        "scenarios": names,
        "missing": missing,
        "trades_not_repriced": dropped,
        "config": {"variance_share": cfg.variance_share, "max_components": cfg.max_components,
                   "uncovered_below": cfg.uncovered_below, "merge_threshold": cfg.merge_threshold},
        **body,
    }
    out = _atomic_write(payload, root, str(report["valuation_date"]), f"coverage_{report.get('run_id')}.json")
    logger.info("Coverage %.2f over %d direction(s) | diversity PR %.2f (target %s) | %d merge pair(s) -> %s",
                body["coverage"], body["n_directions"], body["diversity"]["participation_ratio"],
                cfg.diversity_target, len(body["merge_pairs"]), out)
    for d in body["directions"]:
        logger.info("  PC%-2d var %5.1f%%  spanned %.2f%s  top %s", d["index"], 100 * d["variance_share"],
                    d["spanned_share"], "  UNCOVERED" if d["uncovered"] else "",
                    ", ".join(f"{x['trade_id']}({x['loading']:+.2f})" for x in d["top_loadings"][:3]))
    for p in body["merge_pairs"]:
        logger.info("  merge candidate %s ~ %s (cos %.2f)", p["a"], p["b"], p["cosine"])
    return payload


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="scenario-set coverage and diversity report")
    p.add_argument("--report", default=None, help="stress report JSON; default: the latest")
    p.add_argument("--tier", default=None, help="severity tier; default: coverage.json tier (mild)")
    p.add_argument("--workers", type=int, default=None, help="processes for the full-reval history")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _setup_logging()
    args = _parse_args(argv)
    run_coverage(args.report, tier=args.tier, workers=args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
