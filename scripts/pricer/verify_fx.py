"""End-to-end verification of the FX pricing module against real spots/rates.

Prints recovered spots + foreign rates and a per-trade table (PV, FX delta).

    conda run --no-capture-output -n py312 python scripts/pricer/verify_fx.py
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.fx_adapter import FxMarketDataAdapter
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.ql.factory import get_pricer


def main() -> None:
    root = get_project_root()
    config = load_pricer_config()
    pf = load_portfolio_config()
    ctx = ValuationContext(valuation_date=pf.default_valuation_date)
    print(f"Valuation date: {pf.default_valuation_date}\n")

    adapter = FxMarketDataAdapter(config, root)
    print(f"{'Pair':<10} {'Spot':>10} {'Foreign rate':>13}")
    print("-" * 36)
    for ccy in ("JPY", "TWD"):
        fx, _a, _w = adapter.fx(ccy, ctx)
        print(f"{'USD/'+ccy:<10} {fx.spot:>10.3f} {fx.foreign_rate*100:>12.3f}%")

    loader = MarketDataLoader(config, root)
    print(f"\n{'Trade':<9} {'Type':<12} {'Pair':<9} {'PV (USD)':>15} {'FX delta/1%':>14}  Detail")
    print("-" * 86)
    for trade in load_portfolio(asset_class="fx"):
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        r = get_pricer(trade).price(trade, mds, ctx)
        m = r.measures
        if r.trade_type == "fx_forward":
            detail = f"strike={m['strike']:.2f} fair_fwd={m['fair_forward']:.2f} spot={m['spot']:.2f}"
        else:
            detail = f"contract_fx={m['contract_fx']:.2f} spot={m['spot']:.2f}"
        print(f"{r.trade_id:<9} {r.trade_type:<12} {r.diagnostics['pair']:<9} "
              f"{r.pv:>15,.2f} {m['fx_delta_1pct']:>14,.0f}  {detail}")


if __name__ == "__main__":
    main()
