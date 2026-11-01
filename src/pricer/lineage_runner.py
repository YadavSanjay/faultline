"""Persist calculation-lineage JSON for supported trades (ENG-1 driver).

Builds the explain payload (`src/pricer/lineage.py`) for each supported trade and writes it
to ``data/pricer/lineage/<valuation_date>/<trade_id>.json`` -- the artifact the BFF
``/lineage/{trade_id}`` endpoint serves (V1 stored-only). Reuses the pricing engine and
snapshot, so the lineage is pinned to the same inputs as the priced result.

    python -m src.pricer.lineage_runner
    python -m src.pricer.lineage_runner --trade TRADE_22
    python -m src.pricer.lineage_runner --asset-class rates --valuation-date 2026-05-29

This is a standalone driver in V1; it can later be folded into the main batch runner.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common import storage
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.lineage import build_explain, supported
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.provenance import RunProvenance
from src.pricer.runner import _build_ctx, _setup_logging

logger = logging.getLogger("pricer.lineage_runner")

_PLAIN_ENGLISH = {
    "ois_swap": "PV = discounted fixed coupons minus projected floating coupons, both off the SOFR curve.",
    "stock": "Value = your share quantity times the latest close. Nothing else moves it.",
}


def _atomic_write(location: str, payload: dict) -> None:
    storage.write_text_atomic(location, json.dumps(payload, indent=2, default=str))


def run_lineage(asset_class: str | None = None, valuation_date: date | None = None,
                trade_id: str | None = None, book: str | None = None) -> int:
    root = get_project_root()
    config = load_pricer_config()
    pf_cfg = load_portfolio_config(book=book)
    valuation_date = valuation_date or pf_cfg.default_valuation_date
    ctx = _build_ctx(config, valuation_date, "PV")

    run_prov = RunProvenance.capture(root, config)
    engine = PricingEngine(config, root)
    trades = load_portfolio(asset_class=asset_class, book=book)
    if trade_id:
        trades = [t for t in trades if t.trade_id == trade_id]

    out_dir = storage.join(storage.data_base(root), "pricer", "lineage", valuation_date.isoformat())
    n_written = n_skipped = n_failed = 0
    for trade in trades:
        if not supported(trade.trade_type.value):
            n_skipped += 1
            continue
        try:
            explain = build_explain(engine, trade, ctx)
        except Exception as exc:  # one trade failing shouldn't sink the run
            logger.warning("  %s lineage failed: %s", trade.trade_id, exc)
            n_failed += 1
            continue
        if explain is None:
            n_skipped += 1
            continue
        explain["plain_english"] = _PLAIN_ENGLISH.get(trade.trade_type.value, "")
        explain["provenance"] = {
            "run_id": run_prov.run_id,
            "git_sha": run_prov.git_sha,
            "config_hash": run_prov.config_hash,
            "quantlib_version": run_prov.quantlib_version,
            "market_data_snapshot_id": engine.snapshot_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "pricer_class": type(trade).__name__,
        }
        _atomic_write(storage.join(out_dir, f"{trade.trade_id}.json"), explain)
        n_written += 1
        recon = explain.get("final", {}).get("reconciliation", [])
        ok = all(r.get("ok") for r in recon) if recon else None
        logger.info("  %-10s lineage written (reconciles=%s)", trade.trade_id, ok)

    logger.info("Lineage: %d written, %d skipped (unsupported), %d failed -> %s",
                n_written, n_skipped, n_failed, out_dir)
    return n_written


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="persist calculation-lineage JSON")
    p.add_argument("--asset-class", default=None)
    p.add_argument("--valuation-date", default=None, help="ISO date; default: portfolio default")
    p.add_argument("--trade", default=None, help="one trade id; default: all supported")
    p.add_argument("--book", default=None,
                   help="risk-appetite book: base|balanced|aggressive|risky (default: aggressive)")
    return p.parse_args()


if __name__ == "__main__":
    _setup_logging()
    args = _parse_args()
    vdate = date.fromisoformat(args.valuation_date) if args.valuation_date else None
    run_lineage(asset_class=args.asset_class, valuation_date=vdate, trade_id=args.trade,
                book=args.book)
