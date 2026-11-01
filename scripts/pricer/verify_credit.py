"""End-to-end verification of the CDS pricing module against real DTCC spreads.

Prints per-trade par spread, fair spread, upfront, NPV, CS01 and hazard.

    conda run --no-capture-output -n py312 python scripts/pricer/verify_credit.py
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.ql.factory import get_pricer


def main() -> None:
    root = get_project_root()
    config = load_pricer_config()
    pf = load_portfolio_config()
    ctx = ValuationContext(valuation_date=pf.default_valuation_date,
                           max_data_staleness_days=config.get("max_data_staleness_days", 7))
    print(f"Valuation date: {pf.default_valuation_date}\n")

    loader = MarketDataLoader(config, root)
    print(f"{'Trade':<9} {'Ref':<10} {'Side':<5} {'Par':>7} {'Fair':>7} {'Upfront':>9} "
          f"{'NPV (USD)':>14} {'CS01':>10} {'Hazard':>8}  Source")
    print("-" * 100)
    for trade in load_portfolio(asset_class="credit"):
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        r = get_pricer(trade).price(trade, mds, ctx)
        m = r.measures
        print(f"{r.trade_id:<9} {trade.reference:<10} {trade.protection.value:<5} "
              f"{m['par_spread_bp']:>6.1f} {m['fair_spread_bp']:>6.1f} {m['upfront_pts']:>8.3f} "
              f"{r.pv:>14,.2f} {m['cs01']:>10,.0f} {m['hazard_bp']:>7.1f}  {r.diagnostics['spread_source']}")
        for w in r.warnings:
            print(f"          ! {w}")


if __name__ == "__main__":
    main()
