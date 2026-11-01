"""Dispatch a domain Trade to its concrete QuantLib pricer."""

from __future__ import annotations

from ...input.common.enums import TradeType
from ...input.common.trade import Trade
from ..common.base_pricer import BasePricer
from .cap_pricer import CapPricer
from .swap_pricer import OISSwapPricer
from .swaption_pricer import BermudanSwaptionPricer, EuropeanSwaptionPricer

_PRICERS: dict[TradeType, type[BasePricer]] = {
    TradeType.OIS_SWAP: OISSwapPricer,
    TradeType.EUROPEAN_SWAPTION: EuropeanSwaptionPricer,
    TradeType.BERMUDAN_SWAPTION: BermudanSwaptionPricer,
    TradeType.CAP: CapPricer,
}


def get_pricer(trade: Trade) -> BasePricer:
    try:
        return _PRICERS[trade.trade_type]()
    except KeyError as exc:
        raise NotImplementedError(
            f"No pricer registered for trade_type={trade.trade_type!r} "
            f"(trade {trade.trade_id})"
        ) from exc
