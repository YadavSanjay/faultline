"""Dispatch an FX Trade to its QuantLib pricer."""

from __future__ import annotations

from ...input.common.enums import TradeType
from ...input.common.trade import Trade
from ..common.base_pricer import BasePricer
from .fx_pricer import CrossCurrencySwapPricer, FxForwardPricer

_PRICERS: dict[TradeType, type[BasePricer]] = {
    TradeType.FX_FORWARD: FxForwardPricer,
    TradeType.XCCY_SWAP: CrossCurrencySwapPricer,
}


def get_pricer(trade: Trade) -> BasePricer:
    try:
        return _PRICERS[trade.trade_type]()
    except KeyError as exc:
        raise NotImplementedError(
            f"No FX pricer for trade_type={trade.trade_type!r} (trade {trade.trade_id})"
        ) from exc
