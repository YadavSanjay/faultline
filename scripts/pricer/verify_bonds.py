"""End-to-end verification of the bond pricing module against real market data.

Prices trades #15-21 from data/marketdata/, prints a per-bond table (price, YTM,
duration, credit spread / source) and any data warnings. Run with:

    conda run --no-capture-output -n py312 python scripts/pricer/verify_bonds.py
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
    vdate = pf.default_valuation_date
    ctx = ValuationContext(valuation_date=vdate,
                           max_data_staleness_days=config.get("max_data_staleness_days", 7))

    print(f"Valuation date: {vdate}   type: {pf.default_valuation_type}\n")
    loader = MarketDataLoader(config, root)
    trades = load_portfolio(asset_class="bonds")

    print(f"{'Trade':<9} {'Issuer/Term':<10} {'Clean':>9} {'YTM':>8} {'ModDur':>7} "
          f"{'DV01':>12}  Credit / source")
    print("-" * 100)
    all_warnings: list[str] = []
    for trade in trades:
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        r = get_pricer(trade).price(trade, mds, ctx)
        all_warnings += r.warnings
        if r.trade_type == "corp_bond":
            tag = trade.issuer
            credit = (f"{r.measures['credit_spread_bp']:.0f}bp "
                      f"z={r.measures['z_spread_bp']:.0f}bp "
                      f"[{r.diagnostics['credit_spread_source']}]")
        else:
            tag = trade.term
            credit = "UST (par-curve)"
        print(f"{r.trade_id:<9} {tag:<10} {r.measures['clean_price']:>9.3f} "
              f"{r.measures['ytm']*100:>7.3f}% {r.measures['mod_duration']:>7.2f} "
              f"{r.measures['dv01']:>12,.0f}  {credit}")

    print()
    if all_warnings:
        print("Data warnings:")
        for w in sorted(set(all_warnings)):
            print(f"  ! {w}")
    else:
        print("No data warnings.")


if __name__ == "__main__":
    main()
