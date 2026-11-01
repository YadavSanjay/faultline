"""Base pricer: shared QuantLib setup + the price() contract.

Subclasses (one per instrument family) implement `_price`. The base handles the
cross-cutting concerns: setting the global evaluation date, resolving the
discount/forecast curves from the MarketDataSet (multi-curve), and propagating
loader warnings into the result.
"""

from __future__ import annotations

import abc

import QuantLib as ql

from ...input.common.enums import CurvePurpose
from ...input.common.market_data import MarketDataSet, ParOISCurveData
from ...input.common.trade import Trade
from ...input.common.valuation import ValuationContext
from . import conventions as cv
from .curve_builder import build_ois_curve
from .pricing_result import PricingResult


class BasePricer(abc.ABC):
    """Abstract base for all instrument pricers."""

    def price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        # Global evaluation date — every QL object reads this.
        ql.Settings.instance().evaluationDate = cv.to_ql_date(ctx.valuation_date)
        result = self._price(trade, mds, ctx)
        # propagate data warnings collected by the loader
        result.warnings.extend(mds.warnings)
        return result

    @abc.abstractmethod
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        raise NotImplementedError

    # -- shared helpers ------------------------------------------------------ #
    @staticmethod
    def _resolve_ois_curves(
        mds: MarketDataSet, currency: str, index_name: str
    ) -> tuple[ql.YieldTermStructureHandle, ql.YieldTermStructureHandle, ParOISCurveData]:
        """Return (discount_handle, forecast_handle, discount_curve_data).

        For SOFR OIS the two roles share par data, so we build one curve object and
        hand the same handle back for both — but the call sites keep them distinct,
        so a genuine basis/XCCY forecast curve drops in here with no caller change.
        """
        disc_data = mds.curve(currency, CurvePurpose.DISCOUNT, index_name)
        fcst_data = mds.curve(currency, CurvePurpose.FORECAST, index_name)

        if disc_data is fcst_data or (
            disc_data.tenors == fcst_data.tenors
            and disc_data.par_rates == fcst_data.par_rates
        ):
            handle = build_ois_curve(disc_data)
            return handle, handle, disc_data

        # Genuinely different curves (future: Term-SOFR basis, XCCY).
        return build_ois_curve(disc_data), build_ois_curve(fcst_data), disc_data
