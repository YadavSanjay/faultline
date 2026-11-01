"""S1 factor-vector tests: a canonical vector lowers to the correct bump transforms."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.input.common.conventions import IndexConvention
from src.pricer.input.common.market_data import (
    EquitySpotData,
    FxData,
    ParOISCurveData,
)
from src.scenariogen.factor_vector import MaterializedScenario, vector_to_transforms

VDATE = date(2026, 5, 29)


def _apply(transforms: list, item: object) -> object:
    for tf in transforms:
        item = tf(item)
    return item


def test_ir_vector_interpolates_curve():
    curve = ParOISCurveData(currency="USD", index=IndexConvention.usd_sofr(),
                            as_of_date=VDATE, tenors=("2Y", "5Y", "10Y"),
                            par_rates=(0.040, 0.040, 0.040), source="t")
    tfs = vector_to_transforms({"ir:DGS2": 0.0010, "ir:DGS10": 0.0020})
    out = _apply(tfs, curve)
    new = dict(zip(out.tenors, out.par_rates, strict=True))
    assert new["2Y"] == pytest.approx(0.0410, abs=1e-12)    # +10bp at 2Y node
    assert new["10Y"] == pytest.approx(0.0420, abs=1e-12)   # +20bp at 10Y node
    assert new["5Y"] == pytest.approx(0.040 + 0.0010 + (5 - 2) / (10 - 2) * 0.0010, abs=1e-12)


def test_ir_nodes_sorted_before_interp():
    """Nodes supplied out of tenor order still interpolate correctly (sorted internally)."""
    curve = ParOISCurveData(currency="USD", index=IndexConvention.usd_sofr(),
                            as_of_date=VDATE, tenors=("2Y", "10Y"),
                            par_rates=(0.04, 0.04), source="t")
    out = _apply(vector_to_transforms({"ir:DGS10": 0.0020, "ir:DGS2": 0.0010}), curve)
    new = dict(zip(out.tenors, out.par_rates, strict=True))
    assert new["2Y"] == pytest.approx(0.0410, abs=1e-12)
    assert new["10Y"] == pytest.approx(0.0420, abs=1e-12)


def test_equity_named_and_systematic_default():
    tfs = vector_to_transforms({"eq:NVDA": -0.30, "eq:XLK": -0.10})
    nvda = EquitySpotData("NVDA", "USD", 100.0, VDATE)
    tsm = EquitySpotData("TSM", "USD", 50.0, VDATE)       # not named -> systematic default
    assert _apply(tfs, nvda).spot == pytest.approx(70.0)  # named -30%
    assert _apply(tfs, tsm).spot == pytest.approx(45.0)   # default (XLK) -10%


def test_fx_per_pair():
    tfs = vector_to_transforms({"fx:TWD": 0.08})
    twd = FxData("TWD", 31.0, 0.02, VDATE)
    jpy = FxData("JPY", 150.0, 0.001, VDATE)              # not shocked
    assert _apply(tfs, twd).spot == pytest.approx(31.0 * 1.08)
    assert _apply(tfs, jpy).spot == pytest.approx(150.0)


def test_materialized_scenario_roundtrip():
    ms = MaterializedScenario(name="t", vector={"eq:NVDA": -0.3, "credit": 0.01},
                              drivers=("eq:NVDA",), severity="adverse",
                              provenance={"method": "conditional_covariance"})
    assert len(ms.transforms()) == 2     # one equity, one credit transform
    d = ms.to_dict()
    assert d["name"] == "t" and d["drivers"] == ["eq:NVDA"]
    assert d["vector"]["credit"] == 0.01


def test_empty_vector_no_transforms():
    assert vector_to_transforms({}) == []
