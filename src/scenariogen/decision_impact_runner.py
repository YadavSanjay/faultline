"""Decision impact: do the scenarios change a sizing decision? (design §7.6, G13).

The scenario losses enter the sizing optimizer (``src/pricer/sizing``) as a stress-loss constraint next to
the VaR budget (``stress_constraint.stress_scale``): the sized book's worst scenario loss may not exceed the
profile's stress budget, ``var_multiple`` x its daily VaR budget. For each scenario set and each profile this
runner sizes the book with and without the constraint and reports what changes: per-trade size multipliers,
the binding constraint, the worst scenario, VaR / ES / expected return / gross leverage, and the scenarios over
budget before and after.

Scenario sets come from reports:

* a stress report (default: the latest) -> one set, its results at the constraint tier;
* a layer-ablation report -> one set per layer subset, plus a "without each source" table comparing each
  all-but-one-layer set with the all-layers set.

Each scenario vector is revalued on the **base** book (the unsized book the multipliers apply to), so the P&L
matrix is exact for every multiplier vector (P&L is linear in position size for a fixed scenario).

Analysis only: no book variant or ``portfolio*.json`` is written, so golden PVs are untouched.

    python -m src.scenariogen.decision_impact_runner                          # latest stress report
    python -m src.scenariogen.decision_impact_runner --ablation-report data/pricer/scenario/<date>/ablation_<id>.json

Writes ``data/pricer/scenario/<date>/decision_<run_id>.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from src.common import storage
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.provenance import RunProvenance
from src.pricer.risk.historical_var import FullRevalVarEngine
from src.pricer.risk.sensitivities import RiskEngine
from src.pricer.runner import _build_ctx, _setup_logging
from src.pricer.sizing.expected_returns import ExpectedReturnModel
from src.pricer.sizing.optimizer import SizingResult, solve_profile
from src.pricer.sizing.profiles import RiskProfile, load_profiles
from src.pricer.sizing.sizing_runner import load_book_raw, load_sizing_config
from src.pricer.sizing.stress_constraint import StressInput, worst_stress
from src.pricer.sizing.trade_risk import TradeRiskModel
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.report import _atomic_write
from src.scenariogen.stress import ScenarioStressEngine

logger = logging.getLogger("scenariogen.decision")

ALL_LAYERS = "all"
NO_LAYERS = "none"


@dataclass(frozen=True)
class ScenarioSet:
    """One set of scenario vectors to constrain sizing with."""

    label: str                                  # "stress" | "all" | "none" | "without:<layer>" | <layers>
    vectors: dict[str, dict[str, float]]        # scenario name -> factor vector
    layers: tuple[str, ...] | None = None       # ablation subset's enabled layers (None for a stress report)


def stress_budget(profile: RiskProfile, capital_base: float, var_multiple: float) -> float:
    """The profile's stress-loss budget: ``var_multiple`` x its daily VaR budget (dollars)."""
    return float(var_multiple) * float(profile.var_budget_pct) * float(capital_base)


def sets_from_stress_report(report: Mapping[str, Any], tier: str) -> list[ScenarioSet]:
    """One scenario set: the report's (non-LLM) results at ``tier``.

    Raises:
        ValueError: No result at ``tier`` carries a materialized vector.
    """
    vectors = {str(r["scenario"]): dict((r.get("materialized") or {}).get("vector") or {})
               for r in report.get("scenarios", [])
               if r.get("severity") == tier and r.get("source") != "llm"}
    vectors = {k: v for k, v in vectors.items() if v}
    if not vectors:
        raise ValueError(f"stress report {report.get('run_id')}: no {tier} result with a materialized vector")
    return [ScenarioSet("stress", vectors)]


def sets_from_ablation_report(report: Mapping[str, Any]) -> list[ScenarioSet]:
    """One scenario set per layer subset (same subset across scenarios), labelled by its layers.

    The full layer set is labelled ``"all"``, the empty one ``"none"`` and each all-but-one subset
    ``"without:<layer>"``; other subsets keep their ``"+"``-joined layer list.

    Raises:
        ValueError: The report has no subset vectors (an ablation report from before vectors were stored).
    """
    order = [str(x) for x in report.get("order", [])]
    full = frozenset(order)
    by_layers: dict[frozenset[str], dict[str, dict[str, float]]] = {}
    for rec in report.get("scenarios", []):
        for sub in rec.get("subsets", []):
            if not sub.get("vector"):
                continue
            by_layers.setdefault(frozenset(sub["layers"]), {})[str(rec["scenario"])] = dict(sub["vector"])
    if not by_layers:
        raise ValueError(f"ablation report {report.get('run_id')}: no subset vectors (re-run the ablation)")

    def label(layers: frozenset[str]) -> str:
        if layers == full:
            return ALL_LAYERS
        if not layers:
            return NO_LAYERS
        missing = full - layers
        if len(missing) == 1:
            return f"without:{next(iter(missing))}"
        return "+".join(x for x in order if x in layers)

    return [ScenarioSet(label(k), v, tuple(x for x in order if x in k))
            for k, v in sorted(by_layers.items(), key=lambda kv: (-len(kv[0]), sorted(kv[0])))]


def compare(base: SizingResult, alt: SizingResult, *, tol: float = 1e-9) -> dict[str, Any]:
    """What sizing ``alt`` changes against ``base``: multipliers, binding constraint and book metrics."""
    b = {s.trade_id: s.multiplier for s in base.sizes}
    a = {s.trade_id: s.multiplier for s in alt.sizes}
    changes = {t: {"from": b[t], "to": a.get(t, 0.0), "ratio": (a.get(t, 0.0) / b[t]) if b[t] else None}
               for t in b if abs(a.get(t, 0.0) - b[t]) > tol * max(1.0, abs(b[t]))}
    return {
        "binding": {"from": base.binding_constraint, "to": alt.binding_constraint},
        "n_trades_changed": len(changes),
        "multipliers": changes,
        "var99": {"from": base.achieved_var99, "to": alt.achieved_var99},
        "es975": {"from": base.achieved_es975, "to": alt.achieved_es975},
        "expected_annual_return": {"from": base.expected_annual_return, "to": alt.expected_annual_return},
        "gross_leverage": {"from": base.gross_leverage, "to": alt.gross_leverage},
    }


def over_budget(multipliers: np.ndarray, pnl: np.ndarray, names: Sequence[str], budget: float) -> list[str]:
    """Scenarios whose book loss under ``multipliers`` exceeds ``budget``."""
    losses = -(pnl @ multipliers)
    return [n for n, loss in zip(names, losses, strict=True) if loss > budget]


def _vector_pnl(vectors: Mapping[str, Mapping[str, float]], price: Callable[[MaterializedScenario], dict[str, float]],
                trade_ids: Sequence[str], cache: dict[tuple, dict[str, float]]) -> tuple[list[str], np.ndarray]:
    names, rows = [], []
    for name, vec in vectors.items():
        key = tuple(sorted((k, round(float(v), 15)) for k, v in vec.items()))
        if key not in cache:
            cache[key] = price(MaterializedScenario(name=name, vector=dict(vec)))
        names.append(name)
        rows.append([cache[key].get(t, 0.0) for t in trade_ids])
    return names, np.asarray(rows, dtype=float)


def run_decision_impact(stress_report: str | None = None, ablation_report: str | None = None) -> dict[str, Any]:
    """Sizes every profile with and without each scenario set's stress-loss constraint and writes the report."""
    root = get_project_root()
    config = load_pricer_config()
    sizing_cfg = load_sizing_config(root)
    sc_cfg = sizing_cfg.get("stress_constraint") or {}
    tier = str(sc_cfg.get("tier", "adverse"))
    var_multiple = float(sc_cfg.get("var_multiple", 6.0))

    reports: list[tuple[str, dict[str, Any]]] = []
    if ablation_report:
        reports.append(("ablation", json.loads(storage.read_text(ablation_report))))
    if stress_report or not ablation_report:
        path = stress_report or _latest(root, "scenario_")
        reports.append(("stress", json.loads(storage.read_text(path))))
    sets: list[tuple[str, ScenarioSet, dict[str, Any]]] = []
    for kind, rep in reports:
        found = sets_from_ablation_report(rep) if kind == "ablation" else sets_from_stress_report(rep, tier)
        sets.extend((kind, s, rep) for s in found)

    vdate = date.fromisoformat(str(reports[0][1]["valuation_date"]))
    run_prov = RunProvenance.capture(root, config)
    ctx = _build_ctx(config, vdate, "PV")
    engine = PricingEngine(config, root)
    trades = load_portfolio(book="base")
    hedge_ids = set(sizing_cfg.get("hedge_trades", []))
    model = TradeRiskModel.build(trades, ctx, RiskEngine(engine, config), config, root, hedge_ids,
                                 int(sizing_cfg.get("lookback_days", 250)))
    pv_by_id = {t.trade_id: t.pv for t in model.trades}
    sens_by_id = {t.trade_id: t.sensitivities for t in model.trades}
    er = ExpectedReturnModel(sizing_cfg).compute(load_book_raw("base")["trades"], pv_by_id, vdate, sens_by_id)
    profiles = load_profiles(sizing_cfg)
    capital_base = float(sizing_cfg["capital_base"])
    pairs = sizing_cfg.get("market_neutral_pairs")
    unconstrained = {k: solve_profile(model, er, p, capital_base, pairs=pairs) for k, p in profiles.items()}

    stress_engine = ScenarioStressEngine(engine, FullRevalVarEngine(engine, config, root))
    by_id = {t.trade_id: t for t in trades}
    model_trades = [by_id[t] for t in model.trade_ids]

    def price(ms: MaterializedScenario) -> dict[str, float]:
        res = stress_engine.run(ms, model_trades, ctx, with_capital=False)
        return {r.trade_id: r.pnl for r in res.results}

    cache: dict[tuple, dict[str, float]] = {}
    out_sets: list[dict[str, Any]] = []
    constrained_by_set: dict[str, dict[str, SizingResult]] = {}
    for kind, sset, rep in sets:
        names, pnl = _vector_pnl(sset.vectors, price, model.trade_ids, cache)
        profiles_out: dict[str, Any] = {}
        constrained_by_set[sset.label] = {}
        for key, prof in profiles.items():
            budget = stress_budget(prof, capital_base, var_multiple)
            free = unconstrained[key]
            m_free = np.array([{s.trade_id: s.multiplier for s in free.sizes}[t] for t in model.trade_ids])
            res = solve_profile(model, er, prof, capital_base, pairs=pairs,
                                stress=StressInput(pnl, tuple(names), budget))
            constrained_by_set[sset.label][key] = res
            m_res = np.array([{s.trade_id: s.multiplier for s in res.sizes}[t] for t in model.trade_ids])
            worst_free = worst_stress(m_free, pnl, names)
            profiles_out[key] = {
                "stress_budget": budget,
                "unconstrained": {"binding": free.binding_constraint, "worst_scenario": worst_free[0],
                                  "worst_loss": worst_free[1], "over_budget": over_budget(m_free, pnl, names, budget)},
                "constrained": {"binding": res.binding_constraint, "worst_scenario": res.worst_stress_scenario,
                                "worst_loss": res.worst_stress_loss,
                                "over_budget": over_budget(m_res, pnl, names, budget)},
                "change": compare(free, res),
            }
            logger.info("  %-18s %-10s budget %6.0fM | worst %-20s %7.1fM -> %7.1fM | binding %s -> %s",
                        sset.label, key, budget / 1e6, worst_free[0], worst_free[1] / 1e6,
                        (res.worst_stress_loss or 0.0) / 1e6, free.binding_constraint, res.binding_constraint)
        out_sets.append({"kind": kind, "label": sset.label, "layers": list(sset.layers) if sset.layers else None,
                         "source_run_id": rep.get("run_id"), "scenarios": names,
                         "scenario_book_pnl_base": dict(zip(names, (pnl.sum(axis=1)).tolist(), strict=True)),
                         "profiles": profiles_out})

    without: dict[str, Any] = {}
    if ALL_LAYERS in constrained_by_set:
        for label, by_prof in constrained_by_set.items():
            if label.startswith("without:"):
                without[label.split(":", 1)[1]] = {
                    k: compare(constrained_by_set[ALL_LAYERS][k], r) for k, r in by_prof.items()}

    payload = {
        "valuation_date": vdate.isoformat(), "generated_at": datetime.now(UTC).isoformat(),
        "run_id": run_prov.run_id, "tier": tier, "var_multiple": var_multiple, "capital_base": capital_base,
        "sources": [{"kind": kind, "run_id": rep.get("run_id"), "snapshot_id": rep.get("snapshot_id"),
                     "cov_id": (rep.get("covariance") or {}).get("cov_id")} for kind, rep in reports],
        "note": ("Analysis only: sized with and without a stress-loss constraint (worst scenario loss <= "
                 f"{var_multiple:g} x daily VaR budget); no book variant is written."),
        "sets": out_sets, "without_each_source": without,
    }
    out = _atomic_write(payload, root, vdate.isoformat(), f"decision_{run_prov.run_id}.json")
    logger.info("Wrote decision-impact report (%d set(s)) -> %s", len(out_sets), out)
    return payload


def _latest(root: Path, prefix: str) -> str:
    base = storage.join(storage.data_base(root), "pricer", "scenario")
    locs = [str(p) for p in storage.glob(base, f"*/{prefix}*.json") if "scenario_llm-" not in str(p)]
    if not locs:
        raise FileNotFoundError(f"no {prefix}*.json report under {base}")
    return max(locs, key=lambda p: json.loads(storage.read_text(p)).get("generated_at", ""))


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="scenario decision impact on sizing")
    p.add_argument("--stress-report", default=None, help="stress report JSON; default: the latest")
    p.add_argument("--ablation-report", default=None, help="layer-ablation report JSON (per-source sets)")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _setup_logging()
    args = _parse_args(argv)
    run_decision_impact(args.stress_report, args.ablation_report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
