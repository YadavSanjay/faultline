"""End-to-end verification of the equity-option pricing module against real chains.

Prints recovered spot / dividend yield per underlier and a per-trade table
(PV, delta, vega, structure). Equities value at the chain snapshot 2026-06-17.

    conda run --no-capture-output -n py312 python scripts/pricer/verify_equity.py
"""

import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.equity_adapter import EquityMarketDataAdapter
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.ql.factory import get_pricer

VDATE = date(2026, 6, 17)


def main() -> None:
    root = get_project_root()
    config = load_pricer_config()
    ctx = ValuationContext(valuation_date=VDATE)
    print(f"Valuation date: {VDATE} (option-chain snapshot)\n")

    adapter = EquityMarketDataAdapter(config, root)
    print(f"{'Underlier':<10} {'Spot':>10} {'Div yld':>8} {'ATM IV(6M)':>11}")
    print("-" * 45)
    for name in ["NVDA", "AAPL", "MSFT", "META", "TSLA", "QQQ", "SPX"]:
        eq, _as_of, _w = adapter.equity_market(name, ctx)
        atm = eq.vol_surface.implied_vol(0.5, eq.spot)
        print(f"{name:<10} {eq.spot:>10.2f} {eq.dividend_yield*100:>7.2f}% {atm*100:>10.1f}%")

    loader = MarketDataLoader(config, root)
    print(f"\n{'Trade':<9} {'Underlier':<8} {'Structure':<12} {'PV (USD)':>14} "
          f"{'Delta(sh)':>11} {'Vega/volpt':>12}")
    print("-" * 78)
    for trade in load_portfolio(asset_class="options"):
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        r = get_pricer(trade).price(trade, mds, ctx)
        print(f"{r.trade_id:<9} {trade.underlier:<8} {trade.structure:<12} "
              f"{r.pv:>14,.2f} {r.measures['delta_shares']:>11,.0f} "
              f"{r.measures['vega_1volpt']:>12,.0f}")


if __name__ == "__main__":
    main()
