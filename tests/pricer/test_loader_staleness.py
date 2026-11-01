"""Input Data Loader staleness behaviour against the real Pensford snapshots."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.rates_adapter import RatesMarketDataAdapter

pytestmark = pytest.mark.needs_market_data


def _adapter() -> RatesMarketDataAdapter:
    return RatesMarketDataAdapter(load_pricer_config(), get_project_root())


def test_fresh_snapshot_no_warning():
    # 2026-05-29 is an on-curve Pensford snapshot -> valuing there is not stale
    ctx = ValuationContext(valuation_date=date(2026, 5, 29))
    curve, as_of, warns = _adapter().par_ois_curve(ctx)
    assert as_of == date(2026, 5, 29)
    assert warns == []
    assert len(curve.par_rates) == len(curve.tenors) >= 6


def test_future_valuation_is_stale():
    ctx = ValuationContext(valuation_date=date(2027, 1, 15), max_data_staleness_days=7)
    _, as_of, warns = _adapter().par_ois_curve(ctx)
    assert as_of == date(2026, 10, 6)        # latest available Pensford snapshot
    assert any("stale" in w.lower() for w in warns)


def test_before_history_uses_earliest_with_warning():
    ctx = ValuationContext(valuation_date=date(2020, 1, 1))
    _, as_of, warns = _adapter().par_ois_curve(ctx)
    assert as_of == date(2021, 7, 30)        # earliest available
    assert any("on/before" in w for w in warns)
