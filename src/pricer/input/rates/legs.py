"""Coupon legs for rates instruments (composition units).

A fixed/overnight leg pair composes into an OIS swap; a standalone overnight leg
composes into a cap/floor.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..common.conventions import IndexConvention, ScheduleConvention
from ..common.enums import LegType
from ..common.trade import Leg


@dataclass
class FixedLeg(Leg):
    """Fixed-rate coupon stream."""
    rate: float                         # decimal coupon, e.g. 0.038
    schedule: ScheduleConvention = None  # type: ignore[assignment]
    leg_type: LegType = LegType.FIXED


@dataclass
class OvernightLeg(Leg):
    """Compounded overnight (RFR / SOFR) coupon stream."""
    index: IndexConvention = None        # type: ignore[assignment]
    schedule: ScheduleConvention = None  # type: ignore[assignment]
    spread: float = 0.0                  # additive spread over compounded RFR
    leg_type: LegType = LegType.OVERNIGHT
