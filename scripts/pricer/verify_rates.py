"""End-to-end verification of the rates pricing module against real market data.

Prices trades #22-25 from data/marketdata/rates/, prints the derived SOFR par
curve, a per-trade PV/measures table, and any staleness warnings. Run with:

    conda run --no-capture-output -n py312 python scripts/pricer/verify_rates.py
"""

import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.loader.rates_adapter import RatesMarketDataAdapter
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.ql.rates.pricer_factory import get_pricer


def main() -> None:
    root = get_project_root()
    config = load_pricer_config()
    pf = load_portfolio_config()
    vdate = pf.default_valuation_date
    ctx = ValuationContext(valuation_date=vdate, max_data_staleness_days=config.get("max_data_staleness_days", 7))

    print(f"Valuation date: {vdate}   type: {pf.default_valuation_type}\n")

    # Derived SOFR par curve
    curve, as_of, warns = RatesMarketDataAdapter(config, root).par_ois_curve(ctx)
    print(f"Derived SOFR OIS par curve (source snapshot {as_of}):")
    for t, r in zip(curve.tenors, curve.par_rates):
        print(f"    {t:>4}  {r*100:7.4f}%")
    print()

    loader = MarketDataLoader(config, root)
    trades = load_portfolio(asset_class="rates")

    print(f"{'Trade':<9} {'Type':<19} {'PV (USD)':>16}  Key measures")
    print("-" * 100)
    all_warnings: list[str] = []
    for trade in trades:
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        r = get_pricer(trade).price(trade, mds, ctx)
        all_warnings += r.warnings
        if trade.trade_type.value == "ois_swap":
            extra = f"fair={r.measures['fair_rate']*100:.4f}%  dv01={r.measures['dv01']:,.0f}/bp"
        elif trade.trade_type.value == "bermudan_swaption":
            extra = (f"strike={r.measures['atm_or_strike']*100:.3f}%  "
                     f"HW a={r.measures['hw_mean_reversion']:.4f} sig={r.measures['hw_sigma']:.4f}  "
                     f"tree/FD={r.measures['tree_fd_rel_diff']*100:.2f}%")
        else:
            extra = (f"strike={r.measures['atm_or_strike']*100:.3f}%  "
                     f"vol={r.measures['normal_vol_bp']:.0f}bp  "
                     f"vega={r.measures.get('vega_1bp', 0):,.0f}/bp")
        print(f"{r.trade_id:<9} {r.trade_type:<19} {r.pv:>16,.2f}  {extra}")

    print()
    if all_warnings:
        print("Data warnings:")
        for w in sorted(set(all_warnings)):
            print(f"  ! {w}")
    else:
        print("No data staleness warnings.")


if __name__ == "__main__":
    main()
