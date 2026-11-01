"""Map pure-domain enums/conventions to concrete QuantLib objects.

Centralising these mappings keeps the rest of the QL layer declarative and makes
adding a new calendar/day-count a one-line change.
"""

from __future__ import annotations

from datetime import date

import QuantLib as ql

from ...input.common.conventions import IndexConvention
from ...input.common.enums import (
    BusinessDayConvention,
    CalendarCode,
    DayCount,
    Frequency,
)

# --------------------------------------------------------------------------- #
# Day counts
# --------------------------------------------------------------------------- #
_DAY_COUNTS = {
    DayCount.ACT_360: ql.Actual360(),
    DayCount.ACT_365F: ql.Actual365Fixed(),
    DayCount.ACT_ACT_ISDA: ql.ActualActual(ql.ActualActual.ISDA),
    DayCount.THIRTY_360: ql.Thirty360(ql.Thirty360.BondBasis),
    DayCount.ACT_ACT_BOND: ql.ActualActual(ql.ActualActual.Bond),
}


def to_day_count(dc: DayCount) -> ql.DayCounter:
    return _DAY_COUNTS[dc]


# --------------------------------------------------------------------------- #
# Calendars
# --------------------------------------------------------------------------- #
def to_calendar(code: CalendarCode) -> ql.Calendar:
    if code in (CalendarCode.US_GOVERNMENT_BOND,):
        return ql.UnitedStates(ql.UnitedStates.GovernmentBond)
    if code in (CalendarCode.US_SOFR, CalendarCode.US_SETTLEMENT):
        # SOFR fixes on the SIFMA/government-securities calendar; QL exposes it as
        # the GovernmentBond calendar which is the correct one for USD OIS.
        return ql.UnitedStates(ql.UnitedStates.GovernmentBond)
    if code == CalendarCode.TARGET:
        return ql.TARGET()
    if code == CalendarCode.JAPAN:
        return ql.Japan()
    if code == CalendarCode.TAIWAN:
        return ql.Taiwan()
    raise KeyError(f"Unmapped calendar code: {code}")


# --------------------------------------------------------------------------- #
# Frequencies
# --------------------------------------------------------------------------- #
_FREQUENCIES = {
    Frequency.ANNUAL: ql.Annual,
    Frequency.SEMIANNUAL: ql.Semiannual,
    Frequency.QUARTERLY: ql.Quarterly,
    Frequency.MONTHLY: ql.Monthly,
    Frequency.ONCE: ql.Once,
}

_FREQ_PERIODS = {
    Frequency.ANNUAL: ql.Period(1, ql.Years),
    Frequency.SEMIANNUAL: ql.Period(6, ql.Months),
    Frequency.QUARTERLY: ql.Period(3, ql.Months),
    Frequency.MONTHLY: ql.Period(1, ql.Months),
}


def to_frequency(freq: Frequency) -> int:
    return int(_FREQUENCIES[freq])


def to_period(freq: Frequency) -> ql.Period:
    return _FREQ_PERIODS[freq]


# --------------------------------------------------------------------------- #
# Business-day conventions
# --------------------------------------------------------------------------- #
_BDC = {
    BusinessDayConvention.FOLLOWING: ql.Following,
    BusinessDayConvention.MODIFIED_FOLLOWING: ql.ModifiedFollowing,
    BusinessDayConvention.PRECEDING: ql.Preceding,
    BusinessDayConvention.MODIFIED_PRECEDING: ql.ModifiedPreceding,
    BusinessDayConvention.UNADJUSTED: ql.Unadjusted,
}


def to_bdc(bdc: BusinessDayConvention) -> int:
    return int(_BDC[bdc])


# --------------------------------------------------------------------------- #
# Tenor strings <-> ql.Period
# --------------------------------------------------------------------------- #
def to_ql_period(tenor: str) -> ql.Period:
    """Parse '10Y', '3M', '0D' -> ql.Period."""
    return ql.Period(tenor)


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def to_ql_date(d: date) -> ql.Date:
    """datetime.date -> ql.Date."""
    return ql.Date(d.day, d.month, d.year)


def from_ql_date(d: ql.Date) -> date:
    return date(d.year(), d.month(), d.dayOfMonth())


# --------------------------------------------------------------------------- #
# Overnight index
# --------------------------------------------------------------------------- #
def to_overnight_index(index: IndexConvention,
                       forwarding: ql.YieldTermStructureHandle | None = None) -> ql.OvernightIndex:
    """Build the QuantLib overnight index for the given convention.

    Currently supports USD-SOFR (the only RFR in trades #22-25).
    """
    name = index.name.upper()
    handle = forwarding if forwarding is not None else ql.YieldTermStructureHandle()
    if name in ("USD-SOFR", "SOFR", "USD SOFR"):
        return ql.Sofr(handle)
    raise KeyError(f"Unmapped overnight index: {index.name}")
