"""Dispatch a bond Trade to its concrete QuantLib pricer."""

from __future__ import annotations

from ...input.common.enums import TradeType
from ...input.common.trade import Trade
from ..common.base_pricer import BasePricer
from .bond_pricer import CorporateBondPricer, TreasuryBondPricer

_PRICERS: dict[TradeType, type[BasePricer]] = {
    TradeType.CORP_BOND: CorporateBondPricer,
    TradeType.UST_BOND: TreasuryBondPricer,
}


def get_pricer(trade: Trade) -> BasePricer:
    try:
        return _PRICERS[trade.trade_type]()
    except KeyError as exc:
        raise NotImplementedError(
            f"No bond pricer for trade_type={trade.trade_type!r} (trade {trade.trade_id})"
        ) from exc
