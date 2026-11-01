"""End-to-end verification of the cash-equity pricing module against real prices.

Prints a per-position mark table (spot, quantity, market value, dividend yield).

    conda run --no-capture-output -n py312 python scripts/pricer/verify_cash_equity.py
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
    ctx = ValuationContext(valuation_date=pf.default_valuation_date)
    print(f"Valuation date: {pf.default_valuation_date}\n")

    loader = MarketDataLoader(config, root)
    print(f"{'Trade':<9} {'Ticker':<6} {'Dir':<6} {'Spot':>10} {'Quantity':>12} "
          f"{'Market value':>16} {'Div yld':>8}")
    print("-" * 76)
    total = 0.0
    for trade in load_portfolio(asset_class="equity"):
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        r = get_pricer(trade).price(trade, mds, ctx)
        total += r.pv
        print(f"{r.trade_id:<9} {trade.ticker:<6} {r.diagnostics['direction']:<6} "
              f"{r.measures['spot']:>10.2f} {r.measures['quantity']:>12,.0f} "
              f"{r.pv:>16,.2f} {r.measures['dividend_yield']*100:>7.2f}%")
    print("-" * 76)
    print(f"{'NET':<9} {'':<6} {'':<6} {'':>10} {'':>12} {total:>16,.2f}")


if __name__ == "__main__":
    main()
