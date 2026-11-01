"""Unit tests for the F3 spread-basis bumps (swap-spread, CDS-bond).

Data-free: the transforms are applied to constructed market-data items, asserting each moves ONLY
its own leg of the basis and leaves the other curves/spreads untouched -- the isolation that makes
these a *basis* shock rather than a level shock.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.input.common.market_data import (
    CdsSpreadData,
    CreditSpreadData,
    ParOISCurveData,
    TreasuryCurveData,
)
from src.pricer.risk import bumps

_D = date(2026, 7, 10)


def _treasury() -> TreasuryCurveData:
    return TreasuryCurveData(currency="USD", as_of_date=_D, tenors=("2Y", "10Y"),
                             par_yields=(0.040, 0.0437))


def _ois() -> ParOISCurveData:
    return ParOISCurveData(currency="USD", index="SOFR", as_of_date=_D, tenors=("2Y", "10Y"),
                           par_rates=(0.038, 0.0395))


def _single_name_cds() -> CdsSpreadData:
    return CdsSpreadData(reference="ORCL", currency="USD", spread=0.0177, recovery=0.40,
                         as_of_date=_D, curve_points=((1.0, 0.0044), (5.0, 0.0177)))


def _index_cds() -> CdsSpreadData:
    return CdsSpreadData(reference="CDX.NA.IG", currency="USD", spread=0.0060, recovery=0.40,
                         as_of_date=_D, n_constituents=125)


def _bond_zspread() -> CreditSpreadData:
    return CreditSpreadData(issuer="ORCL", currency="USD", spread=0.0135, as_of_date=_D)


# --------------------------------------------------------------------------- #
# swap spread: Treasury moves, OIS does not
# --------------------------------------------------------------------------- #
def test_swap_spread_moves_treasury_only():
    tf = bumps.shift_swap_spread(0.0030)
    t = tf(_treasury())
    assert isinstance(t, TreasuryCurveData)
    assert t.par_yields == pytest.approx((0.043, 0.0467))  # +30bp on every node
    ois = tf(_ois())
    assert ois.par_rates == (0.038, 0.0395)                # OIS untouched -> the spread widened


def test_swap_spread_key_rate():
    t = bumps.shift_swap_spread(0.0030, tenor="10Y")(_treasury())
    assert t.par_yields == pytest.approx((0.040, 0.0467))  # only the 10Y node moved


# --------------------------------------------------------------------------- #
# CDS-bond basis: single-name CDS moves; index CDS and bond Z-spread do not
# --------------------------------------------------------------------------- #
def test_cds_bond_moves_single_name_cds_only():
    tf = bumps.shift_cds_bond_basis(0.0060)
    c = tf(_single_name_cds())
    assert c.spread == 0.0177 + 0.0060
    assert c.curve_points == ((1.0, 0.0044 + 0.0060), (5.0, 0.0177 + 0.0060))   # whole curve shifts


def test_cds_bond_leaves_index_and_bond_untouched():
    tf = bumps.shift_cds_bond_basis(0.0060)
    assert tf(_index_cds()).spread == 0.0060               # index (n_constituents set) -> untouched
    assert tf(_bond_zspread()).spread == 0.0135            # bond Z-spread -> untouched (the reference leg)


def test_basis_bumps_do_not_cross_contaminate():
    # swap-spread must not touch credit items; cds-bond must not touch rate curves
    assert bumps.shift_swap_spread(0.003)(_single_name_cds()).spread == 0.0177
    assert bumps.shift_cds_bond_basis(0.006)(_treasury()).par_yields == (0.040, 0.0437)
