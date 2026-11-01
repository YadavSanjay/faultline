"""Model-governance CLI gate (P4).

Runs the three controls over the selected book and writes a provenance-stamped
governance report: the golden-PV regression, independent price verification, and
model-inventory coverage. Exits non-zero if any gate fails, so it can guard CI / EOD.

    python -m src.pricer.governance_runner
    python -m src.pricer.governance_runner --valuation-date 2026-05-29
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.governance.golden import check_golden
from src.pricer.governance.ipv import IpvEngine
from src.pricer.governance.model_inventory import ModelInventory
from src.pricer.governance.report import GovernanceReport, write_governance_report
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.provenance import RunProvenance
from src.pricer.runner import _build_ctx, _setup_logging

logger = logging.getLogger("pricer.governance")


def run_governance(asset_class: str | None = None,
                   valuation_date: date | None = None) -> GovernanceReport:
    """Run the governance gate and write its report under the project root (see `run_governance_to`)."""
    return run_governance_to(get_project_root(), asset_class=asset_class, valuation_date=valuation_date)


def run_governance_to(output_root: Path, *, input_root: Path | None = None,
                      asset_class: str | None = None,
                      valuation_date: date | None = None) -> GovernanceReport:
    """Run governance like `run_governance`, reading inputs from `input_root`, writing under `output_root`.

    The governance report goes to `pricer/governance/<date>/governance_<run_id>.json` under
    `storage.data_base(output_root)`; nothing under `input_root` is written.

    Args:
        output_root: Root whose data base receives the governance report.
        input_root: Root whose data base supplies market data; None means the project root.
        asset_class: Optional asset-class filter.
        valuation_date: Valuation date; None means the portfolio default.

    Returns:
        The governance report.
    """
    in_root = input_root if input_root is not None else get_project_root()
    config = load_pricer_config()
    pf_cfg = load_portfolio_config()
    valuation_date = valuation_date or pf_cfg.default_valuation_date
    ctx = _build_ctx(config, valuation_date, "PV")
    run_prov = RunProvenance.capture(in_root, config)

    engine = PricingEngine(config, in_root)
    trades = load_portfolio(asset_class=asset_class)
    logger.info("Governance run %s | valuation_date=%s | %d trade(s) | snapshot=%s",
                run_prov.run_id, valuation_date, len(trades), engine.snapshot_id)

    # 1. Golden / regression PVs
    pvs = {o.trade_id: o.result.pv
           for o in engine.price_portfolio(trades, ctx) if o.result is not None}
    golden = check_golden(pvs, engine.snapshot_id)
    if not golden.snapshot_matches:
        logger.warning("GOLDEN snapshot mismatch: run=%s vs baseline — re-baseline after review",
                       engine.snapshot_id)
    for b in golden.breaks:
        logger.error("GOLDEN BREAK %s: expected %s got %s (%.2e)",
                     b.trade_id, f"{b.expected:,.2f}", f"{b.actual:,.2f}", b.rel_diff)
    logger.info("Golden: %s (%d checked, %d breaks, %d missing)",
                "PASS" if golden.passed else "FAIL", golden.n_checked,
                len(golden.breaks), len(golden.missing))

    # 2. Independent price verification
    ipv = IpvEngine(engine).verify(trades, ctx)
    for c in ipv.failures():
        logger.error("IPV FAIL %s [%s] %s: primary=%s independent=%s (%.2e)",
                     c.trade_id, c.kind, c.control, f"{c.primary:,.4f}",
                     "-" if c.independent is None else f"{c.independent:,.4f}", c.rel_diff)
    logger.info("IPV: %s (%d controls, %d failed)",
                "PASS" if ipv.all_passed else "FAIL", ipv.n_total, ipv.n_failed)

    # 3. Model-inventory coverage
    inventory = ModelInventory.load()
    covered, missing = inventory.coverage([t.trade_type.value for t in trades])
    for tt in missing:
        logger.error("MODEL INVENTORY GAP: no governed entry for trade-type %r", tt)
    logger.info("Model inventory: %s (%d covered, %d missing)",
                "PASS" if not missing else "FAIL", len(covered), len(missing))

    report = GovernanceReport(valuation_date.isoformat(), run_prov.run_id, engine.snapshot_id,
                              golden, ipv, covered, missing)
    out = write_governance_report(report, output_root)
    logger.info("=" * 60)
    logger.info("Governance %s — report -> %s", "PASS" if report.passed else "FAIL", out)
    return report




def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="QuantLib model-governance gate")
    p.add_argument("--asset-class", default=None)
    p.add_argument("--valuation-date", default=None, help="ISO date; default: portfolio default")
    return p.parse_args()


if __name__ == "__main__":
    _setup_logging()
    args = _parse_args()
    vdate = date.fromisoformat(args.valuation_date) if args.valuation_date else None
    result = run_governance(asset_class=args.asset_class, valuation_date=vdate)
    sys.exit(0 if result.passed else 1)
