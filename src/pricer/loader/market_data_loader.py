"""Generic Input Data Loader.

Resolves a `MarketDataRequest` into a `MarketDataSet` by dispatching each
requirement to the right asset-class adapter. Curve/vol builds are memoised within
a single resolve call so the DISCOUNT and FORECAST requirements of a SOFR trade
reuse one bootstrap (and one staleness warning).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, cast

from ..input.common.enums import MarketDataKind
from ..input.common.market_data import (
    MarketDataRequest,
    MarketDataRequirement,
    MarketDataSet,
)
from ..input.common.valuation import ValuationContext
from .bonds_adapter import BondsMarketDataAdapter
from .cash_equity_adapter import CashEquityMarketDataAdapter
from .credit_adapter import CreditMarketDataAdapter
from .equity_adapter import EquityMarketDataAdapter
from .fx_adapter import FxMarketDataAdapter
from .rates_adapter import RatesMarketDataAdapter
from .snapshot import MarketDataSnapshot


class MarketDataLoader:
    def __init__(
        self,
        config: dict[str, Any],
        project_root: Path,
        snapshot: MarketDataSnapshot | None = None,
    ):
        self.config = config
        self.root = project_root
        # One immutable, content-addressed snapshot of the inputs for this loader's
        # lifetime; built if not injected so every code path gets a snapshot_id.
        self.snapshot = snapshot or MarketDataSnapshot.build(config, project_root)
        self.rates = RatesMarketDataAdapter(config, project_root, self.snapshot)
        self.bonds = BondsMarketDataAdapter(config, project_root, self.snapshot)
        self.equity = EquityMarketDataAdapter(config, project_root, self.snapshot)
        self.cash_equity = CashEquityMarketDataAdapter(config, project_root, self.snapshot)
        self.credit = CreditMarketDataAdapter(config, project_root, self.snapshot)
        self.fx = FxMarketDataAdapter(config, project_root, self.snapshot)

    def resolve(self, request: MarketDataRequest, ctx: ValuationContext) -> MarketDataSet:
        mds = MarketDataSet(
            valuation_date=request.valuation_date,
            snapshot_id=self.snapshot.snapshot_id,
        )
        cache: dict[tuple[Any, ...], tuple[Any, ...]] = {}
        seen_warnings: set[str] = set()

        for req in request.requirements:
            data, as_of, warns = self._resolve_one(req, ctx, cache)
            mds.put(req, data, as_of)
            for w in warns:
                if w not in seen_warnings:
                    seen_warnings.add(w)
                    mds.warn(w)
        return mds

    def _resolve_one(
        self,
        req: MarketDataRequirement,
        ctx: ValuationContext,
        cache: dict[tuple[Any, ...], tuple[Any, ...]],
    ) -> tuple[Any, date, list[str]]:
        if req.kind == MarketDataKind.OIS_CURVE and req.currency == "USD":
            key: tuple[Any, ...] = ("ois_curve", req.currency)
            if key not in cache:
                cache[key] = self.rates.par_ois_curve(ctx)
            return cache[key]

        if req.kind == MarketDataKind.NORMAL_VOL and req.currency == "USD":
            key = ("normal_vol", req.currency, req.tenor)
            if key not in cache:
                vol, as_of = self.rates.normal_vol(req.tenor, ctx)
                cache[key] = (vol, as_of, [])
            return cache[key]

        if req.kind == MarketDataKind.TREASURY_CURVE and req.currency == "USD":
            key = ("treasury_curve", req.currency)
            if key not in cache:
                cache[key] = self.bonds.treasury_curve(ctx)
            return cache[key]

        if req.kind == MarketDataKind.CREDIT_SPREAD and req.currency == "USD":
            key = ("credit_spread", req.currency, req.index_name)
            if key not in cache:
                cache[key] = self.bonds.credit_spread(cast(str, req.index_name), ctx)
            return cache[key]

        if req.kind == MarketDataKind.EQUITY_MARKET and req.currency == "USD":
            key = ("equity_market", req.currency, req.index_name)
            if key not in cache:
                cache[key] = self.equity.equity_market(cast(str, req.index_name), ctx)
            return cache[key]

        if req.kind == MarketDataKind.EQUITY_SPOT and req.currency == "USD":
            key = ("equity_spot", req.currency, req.index_name)
            if key not in cache:
                cache[key] = self.cash_equity.equity_spot(cast(str, req.index_name), ctx)
            return cache[key]

        if req.kind == MarketDataKind.CDS_SPREAD and req.currency == "USD":
            key = ("cds_spread", req.currency, req.index_name)
            if key not in cache:
                cache[key] = self.credit.cds_spread(cast(str, req.index_name), ctx)
            return cache[key]

        if req.kind == MarketDataKind.FX:
            key = ("fx", req.index_name)
            if key not in cache:
                cache[key] = self.fx.fx(cast(str, req.index_name), ctx)
            return cache[key]

        raise NotImplementedError(
            f"No loader adapter for requirement {req.key} "
            f"(kind={req.kind}, ccy={req.currency})"
        )
