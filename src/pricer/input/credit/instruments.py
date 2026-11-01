"""Credit default swaps (trades #28-30).

A single `CreditDefaultSwapTrade` covers both single-name (#28 ORCL, #29 META) and
index (#30 CDX.NA.IG) CDS — the index is modelled as one CDS against an
index-implied flat hazard curve (QL doesn't price CDX tranches; the unfunded index
is fine as a portfolio CDS). `reference` is the key used to look up the par spread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from ..common.conventions import IndexConvention
from ..common.enums import (
    AssetClass,
    CdsKind,
    CurvePurpose,
    MarketDataKind,
    ProtectionSide,
    TradeType,
)
from ..common.market_data import MarketDataRequest, MarketDataRequirement
from ..common.trade import Trade
from ..common.valuation import ValuationContext


@dataclass(kw_only=True)
class CreditDefaultSwapTrade(Trade):
    """Standardised CDS: running coupon + ISDA upfront, priced off a flat hazard curve."""
    asset_class: ClassVar[AssetClass] = AssetClass.CREDIT
    trade_type: ClassVar[TradeType] = TradeType.CDS

    reference: str                       # spread-lookup key, e.g. "ORCL", "CDX.NA.IG"
    protection: ProtectionSide
    kind: CdsKind = CdsKind.SINGLE_NAME
    tenor: str = "5Y"
    running_coupon: float = 0.01         # standard IG coupon (100bp); HY = 0.05
    recovery: float = 0.40               # ISDA convention, senior unsecured
    index: IndexConvention = field(default_factory=IndexConvention.usd_sofr)

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        # ISDA discount on the SOFR curve
        req.add(MarketDataRequirement(
            kind=MarketDataKind.OIS_CURVE, currency=self.currency,
            purpose=CurvePurpose.DISCOUNT, index_name=self.index.name,
        ))
        # par CDS spread for the reference entity / index
        req.add(MarketDataRequirement(
            kind=MarketDataKind.CDS_SPREAD, currency=self.currency, index_name=self.reference,
        ))
        return req
