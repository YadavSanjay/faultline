"""Top-level pricer dispatch by asset class.

Keeps the runner asset-class-agnostic: it hands any Trade here and gets the right
pricer, regardless of which asset-class sub-factory owns it.
"""

from __future__ import annotations

from ..input.common.enums import AssetClass
from ..input.common.trade import Trade
from .common.base_pricer import BasePricer


def get_pricer(trade: Trade) -> BasePricer:
    if trade.asset_class == AssetClass.RATES:
        from .rates.pricer_factory import get_pricer as _rates
        return _rates(trade)
    if trade.asset_class == AssetClass.BONDS:
        from .bonds.pricer_factory import get_pricer as _bonds
        return _bonds(trade)
    if trade.asset_class == AssetClass.OPTIONS:
        from .equity.pricer_factory import get_pricer as _equity
        return _equity(trade)
    if trade.asset_class == AssetClass.EQUITY:
        from .cash_equity.pricer_factory import get_pricer as _cash
        return _cash(trade)
    if trade.asset_class == AssetClass.CREDIT:
        from .credit.pricer_factory import get_pricer as _credit
        return _credit(trade)
    if trade.asset_class == AssetClass.FX:
        from .fx.pricer_factory import get_pricer as _fx
        return _fx(trade)
    raise NotImplementedError(
        f"No pricer registered for asset_class={trade.asset_class!r} (trade {trade.trade_id})"
    )
