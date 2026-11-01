"""Dispatch a cash-equity Trade to its pricer."""

from __future__ import annotations

from ...input.common.enums import TradeType
from ...input.common.trade import Trade
from ..common.base_pricer import BasePricer
from .stock_pricer import CashEquityPricer

_PRICERS: dict[TradeType, type[BasePricer]] = {
    TradeType.STOCK: CashEquityPricer,
}


def get_pricer(trade: Trade) -> BasePricer:
    try:
        return _PRICERS[trade.trade_type]()
    except KeyError as exc:
        raise NotImplementedError(
            f"No cash-equity pricer for trade_type={trade.trade_type!r} (trade {trade.trade_id})"
        ) from exc
