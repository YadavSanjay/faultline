"""Position-sizing runner (portfolio construction across risk-appetite profiles).

Computes, for a valuation date, the trade-level risk model + hybrid expected returns,
then solves per-trade position sizes for every profile (balanced / aggressive / risky)
and writes one provenance-stamped sizing report.

    python -m src.pricer.sizing.sizing_runner
    python -m src.pricer.sizing.sizing_runner --valuation-date 2026-05-29
    python -m src.pricer.sizing.sizing_runner --profile balanced
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common import storage
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.portfolio.portfolio_loader import (
    load_portfolio,
    load_portfolio_config,
    resolve_book_path,
)
from src.pricer.provenance import RunProvenance
from src.pricer.risk.sensitivities import RiskEngine
from src.pricer.runner import _build_ctx, _setup_logging
from src.pricer.sizing.book_variants import write_variants
from src.pricer.sizing.expected_returns import ExpectedReturnModel
from src.pricer.sizing.optimizer import SizingResult, solve_profile
from src.pricer.sizing.profiles import load_profiles
from src.pricer.sizing.revaluation import revalue_profiles, scenario_pnl_matrix
from src.pricer.sizing.trade_risk import TradeRiskModel

logger = logging.getLogger("pricer.sizing.runner")


def load_sizing_config(root: Path | None = None) -> dict[str, Any]:
    root = root or get_project_root()
    with open(root / "configs" / "pricer" / "sizing.json", encoding="utf-8") as f:
        cfg: dict[str, Any] = json.load(f)
    return cfg


def load_book_raw(book: str = "base") -> dict[str, Any]:
    """Full raw portfolio dict for a book variant (default: the canonical base book)."""
    with open(resolve_book_path(book), encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)
    return raw


def run_sizing(valuation_date: date | None = None,
               only_profile: str | None = None) -> dict[str, Any]:
    root = get_project_root()
    config = load_pricer_config()
    sizing_cfg = load_sizing_config(root)
    pf_cfg = load_portfolio_config()
    if valuation_date is None:
        cfg_date = sizing_cfg.get("valuation_date")
        valuation_date = date.fromisoformat(cfg_date) if cfg_date \
            else pf_cfg.default_valuation_date

    run_prov = RunProvenance.capture(root, config)
    ctx = _build_ctx(config, valuation_date, "PV")
    engine = PricingEngine(config, root)
    risk = RiskEngine(engine, config)

    # Size the canonical BASE book (the ~$50mm placeholder), NOT the active default — sizing
    # relative to an already-sized book would compound.
    base_raw = load_book_raw("base")
    trades = load_portfolio(book="base")
    raw_trades = base_raw["trades"]
    hedge_ids = set(sizing_cfg.get("hedge_trades", []))
    lookback = int(sizing_cfg.get("lookback_days", 250))
    capital_base = float(sizing_cfg["capital_base"])

    logger.info("Sizing run %s | valuation_date=%s | %d trades | capital_base=%s",
                run_prov.run_id, valuation_date, len(trades), f"{capital_base:,.0f}")

    # 1. Trade-level risk model (per-trade P&L matrix + covariance).
    model = TradeRiskModel.build(trades, ctx, risk, config, root, hedge_ids, lookback)
    logger.info("  trade risk model: %d trades x %d days, %d factors",
                model.n_trades, model.n_days, len(model.factor_columns))

    # 2. Hybrid expected returns.
    pv_by_id = {t.trade_id: t.pv for t in model.trades}
    sens_by_id = {t.trade_id: t.sensitivities for t in model.trades}
    er = ExpectedReturnModel(sizing_cfg).compute(
        raw_trades, pv_by_id, valuation_date, sens_by_id)

    # 3. Solve each profile.
    profiles = load_profiles(sizing_cfg)
    if only_profile:
        profiles = {k: v for k, v in profiles.items() if k == only_profile}
    results: dict[str, SizingResult] = {}
    pairs = sizing_cfg.get("market_neutral_pairs")
    for key, profile in profiles.items():
        res = solve_profile(model, er, profile, capital_base, pairs=pairs)
        results[key] = res
        _log_profile(res)

    # 4. Re-value PV / Risk / Capital under each configuration (base book priced once
    #    per stress scenario, then scaled by each profile's multipliers).
    ones = np.ones(model.n_trades)
    base_ve = model.var_es(ones)
    scen_pnl = scenario_pnl_matrix(engine, trades, ctx, config)
    notional_by_id = {t.trade_id: t.notional for t in model.trades}
    revals = revalue_profiles(results, pv_by_id, notional_by_id, scen_pnl,
                              model.var99(ones), base_ve["es"]["0.975"])
    for key, rv in revals.items():
        logger.info("  revalue %-12s PV=%15s  ES97.5=%13s  worst-stress=%s (%s)",
                    key, f"{rv.total_pv:,.0f}", f"{rv.es975:,.0f}",
                    f"{rv.worst_stress_loss:,.0f}", rv.worst_stress_scenario)

    report = _build_report(run_prov, valuation_date, capital_base, model, er, results)
    report["revaluation"] = {k: v.as_dict() for k, v in revals.items()}
    out = _write_report(root, report, valuation_date, run_prov.run_id)
    logger.info("Wrote sizing report -> %s", out)

    # 5. Write the risk-appetite book variants (base x multipliers) + refresh the active
    #    default (portfolio.json = the configured default profile). Skipped for a single-profile run.
    if not only_profile:
        default_profile = str(sizing_cfg.get("default_profile", "aggressive"))
        written = write_variants(base_raw, results, capital_base, run_prov.run_id,
                                 valuation_date.isoformat(), default_profile,
                                 resolve_book_path("base").parent)
        logger.info("Wrote book variants: %s | active default (portfolio.json) = %s",
                    ", ".join(p.name for p in written.values()), default_profile)
    return report


def _log_profile(res: SizingResult) -> None:
    logger.info("")
    logger.info("== %-24s VaR budget %13s  binding=%s",
                res.label, f"{res.var_budget:,.0f}", res.binding_constraint)
    logger.info("   achieved VaR99=%13s  ES97.5=%13s  gross=%.2fx  E[ret]=%s (%.1f%%)  ret/VaR=%.2f",
                f"{res.achieved_var99:,.0f}", f"{res.achieved_es975:,.0f}",
                res.gross_leverage, f"{res.expected_annual_return:,.0f}",
                100 * res.expected_return_pct, res.return_on_var)
    logger.info("   top sizes:")
    for s in res.sizes[:6]:
        logger.info("     %-9s x%.2f  notl %14s  risk%%=%4.1f  %s",
                    s.trade_id, s.multiplier, f"{s.target_notional:,.0f}",
                    100 * s.risk_share, s.description[:34])


def _build_report(run_prov: RunProvenance, valuation_date: date, capital_base: float,
                  model: TradeRiskModel, er: dict, results: dict[str, SizingResult]
                  ) -> dict[str, Any]:
    return {
        "run_id": run_prov.run_id,
        "git_sha": run_prov.git_sha,
        "valuation_date": valuation_date.isoformat(),
        "capital_base": capital_base,
        "n_trades": model.n_trades,
        "n_days": model.n_days,
        "n_factors": len(model.factor_columns),
        "trades": [t.as_dict() for t in model.trades],
        "expected_returns": {k: v.as_dict() for k, v in er.items()},
        "profiles": {k: v.as_dict() for k, v in results.items()},
    }


def _write_report(root: Path, report: dict[str, Any], valuation_date: date,
                  run_id: str) -> Path | str:
    base = storage.join(storage.data_base(root), "pricer", "sizing", valuation_date.isoformat())
    out = storage.join(base, f"sizing_{run_id}.json")
    text = json.dumps(report, indent=2)
    storage.write_text_atomic(out, text)
    # Also write/refresh a stable "latest" pointer for the BFF/report to read.
    storage.write_text_atomic(storage.join(base, "sizing_latest.json"), text)
    return out if storage.is_remote_uri(out) else Path(out)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Risk-appetite position sizing runner")
    p.add_argument("--valuation-date", default=None, help="ISO date; default: sizing config")
    p.add_argument("--profile", default=None, help="solve only this profile")
    return p.parse_args()


if __name__ == "__main__":
    _setup_logging()
    args = _parse_args()
    vdate = date.fromisoformat(args.valuation_date) if args.valuation_date else None
    run_sizing(valuation_date=vdate, only_profile=args.profile)
