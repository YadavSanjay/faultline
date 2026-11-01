"""Market conventions as pure domain objects (no QuantLib).

These describe *how* a leg or index is structured. The QuantLib layer maps them
to concrete QL objects (calendars, day counters, schedules).
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import (
    BusinessDayConvention,
    CalendarCode,
    DayCount,
    Frequency,
)


@dataclass(frozen=True)
class IndexConvention:
    """Definition of a (overnight) interest-rate index, e.g. USD SOFR.

    `tenor` is the index's native accrual period. For an overnight RFR like SOFR
    this is "1D"; the leg-level compounding frequency lives in ScheduleConvention.
    """
    name: str                       # e.g. "USD-SOFR"
    currency: str                   # ISO 4217, e.g. "USD"
    tenor: str = "1D"               # native fixing tenor
    settlement_days: int = 2        # spot lag
    day_count: DayCount = DayCount.ACT_360
    calendar: CalendarCode = CalendarCode.US_SOFR

    @staticmethod
    def usd_sofr() -> IndexConvention:
        return IndexConvention(
            name="USD-SOFR",
            currency="USD",
            tenor="1D",
            settlement_days=2,
            day_count=DayCount.ACT_360,
            calendar=CalendarCode.US_SOFR,
        )


@dataclass(frozen=True)
class ScheduleConvention:
    """Schedule generation rules for a coupon leg."""
    frequency: Frequency
    calendar: CalendarCode = CalendarCode.US_SOFR
    business_day_convention: BusinessDayConvention = BusinessDayConvention.MODIFIED_FOLLOWING
    termination_bdc: BusinessDayConvention = BusinessDayConvention.MODIFIED_FOLLOWING
    day_count: DayCount = DayCount.ACT_360
    end_of_month: bool = False
    payment_lag: int = 2            # SOFR OIS standard ~2 business days


# Standard USD SOFR OIS conventions (annual, Act/360) — used by trades #22-25.
SOFR_OIS_FIXED_SCHEDULE = ScheduleConvention(
    frequency=Frequency.ANNUAL,
    calendar=CalendarCode.US_SOFR,
    business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    day_count=DayCount.ACT_360,
    payment_lag=2,
)

SOFR_OIS_FLOAT_SCHEDULE = ScheduleConvention(
    frequency=Frequency.ANNUAL,
    calendar=CalendarCode.US_SOFR,
    business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    day_count=DayCount.ACT_360,
    payment_lag=2,
)
