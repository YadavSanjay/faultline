"""Shared pytest fixtures/helpers for the pricing-module tests.

The key helper, `mock_resolve`, builds a `MarketDataSet` by resolving a trade's
*own* declared requirements against injected mock data — exercising the same
contract the real loader uses, but with no CSV files. This is the mockability the
domain layer was designed for.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.pricer.input.common.conventions import IndexConvention
from src.pricer.input.common.enums import MarketDataKind
from src.pricer.input.common.market_data import (
    FlatNormalVolData,
    MarketDataSet,
    ParOISCurveData,
)
from src.pricer.input.common.trade import Trade
from src.pricer.input.common.valuation import ValuationContext

from market_data_marker import skip_without_market_data

VALUATION_DATE = date(2026, 5, 29)

# A smooth, slightly humped synthetic SOFR par curve (decimal).
PAR_TENORS = ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y")
PAR_RATES = (0.0395, 0.0388, 0.0383, 0.0382, 0.0388, 0.0397)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    skip_without_market_data(items)


@pytest.fixture
def ctx() -> ValuationContext:
    return ValuationContext(valuation_date=VALUATION_DATE)


@pytest.fixture
def curve_data() -> ParOISCurveData:
    return ParOISCurveData(
        currency="USD",
        index=IndexConvention.usd_sofr(),
        as_of_date=VALUATION_DATE,
        tenors=PAR_TENORS,
        par_rates=PAR_RATES,
        source="unit-test-synthetic",
    )


@pytest.fixture
def vol_data() -> FlatNormalVolData:
    return FlatNormalVolData(currency="USD", vol=0.0090, as_of_date=VALUATION_DATE)


def mock_resolve(
    trade: Trade,
    ctx: ValuationContext,
    curve_data: ParOISCurveData,
    vol_data: FlatNormalVolData,
) -> MarketDataSet:
    """Resolve a trade's declared requirements against injected mock data."""
    mds = MarketDataSet(valuation_date=ctx.valuation_date)
    for req in trade.market_data_requirements(ctx).requirements:
        if req.kind == MarketDataKind.OIS_CURVE:
            mds.put(req, curve_data, curve_data.as_of_date)
        elif req.kind == MarketDataKind.NORMAL_VOL:
            mds.put(req, vol_data, vol_data.as_of_date)
        else:  # pragma: no cover - guard for future kinds
            raise AssertionError(f"unexpected requirement kind {req.kind}")
    return mds
