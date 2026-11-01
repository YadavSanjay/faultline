"""Base classes for the trade domain model (inheritance + composition roots).

`Trade` is the abstract base every instrument inherits. Instruments are *composed*
of `Leg` objects (a swap holds a fixed + an overnight leg; a swaption holds an
underlying swap; a cap holds a floating leg + strikes).

The single abstract method `market_data_requirements` is the contract the Input
Data Loader fulfils — it lets each trade declare exactly what data it needs,
keeping the loader generic.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import ClassVar

from .enums import AssetClass, TradeType
from .market_data import MarketDataRequest
from .valuation import ValuationContext


@dataclass
class Leg(abc.ABC):
    """Composition unit of an instrument (a cashflow stream)."""
    currency: str
    notional: float


@dataclass
class Trade(abc.ABC):
    """Abstract base for every priceable instrument.

    Concrete subclasses set `asset_class` / `trade_type` and implement
    `market_data_requirements`.
    """
    trade_id: str
    description: str
    currency: str
    notional: float

    #: set as class attributes by concrete subclasses
    asset_class: ClassVar[AssetClass]
    trade_type: ClassVar[TradeType]

    @abc.abstractmethod
    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        """Declare the market data this trade needs to be priced at `ctx`."""
        raise NotImplementedError
