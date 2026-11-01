"""Dispatch an equity-option Trade to its QuantLib pricer."""

from __future__ import annotations

from ...input.common.enums import TradeType
from ...input.common.trade import Trade
from ..common.base_pricer import BasePricer
from .option_pricer import EquityOptionPricer

_PRICERS: dict[TradeType, type[BasePricer]] = {
    TradeType.EQUITY_OPTION: EquityOptionPricer,
}


def get_pricer(trade: Trade) -> BasePricer:
    try:
        return _PRICERS[trade.trade_type]()
    except KeyError as exc:
        raise NotImplementedError(
            f"No equity pricer for trade_type={trade.trade_type!r} (trade {trade.trade_id})"
        ) from exc
