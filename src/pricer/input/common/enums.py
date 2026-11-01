"""Domain enumerations used across the calculation-input layer.

These are intentionally QuantLib-free string enums. The QuantLib layer
(`src/pricer/ql/common/conventions.py`) maps each value to the corresponding
QuantLib object. Keeping them as plain strings makes the domain objects trivially
serialisable (portfolio.json) and mockable.
"""

from enum import Enum


class AssetClass(str, Enum):
    RATES = "rates"
    BONDS = "bonds"
    EQUITY = "equity"
    OPTIONS = "options"
    FX = "fx"
    CREDIT = "credit"


class TradeType(str, Enum):
    """Concrete instrument kinds the pricer factory dispatches on."""
    OIS_SWAP = "ois_swap"               # trade #22
    EUROPEAN_SWAPTION = "european_swaption"  # trade #23
    BERMUDAN_SWAPTION = "bermudan_swaption"  # trade #24
    CAP = "cap"                          # trade #25
    FLOOR = "floor"
    CORP_BOND = "corp_bond"             # trades #15-19 (fixed-rate corporate bullet)
    UST_BOND = "ust_bond"               # trades #20-21 (on-the-run Treasury)
    EQUITY_OPTION = "equity_option"     # trades #7-14 (vanilla / spread / straddle / collar)
    STOCK = "stock"                     # trades #1-6 (cash equity / ETF long or short)
    CDS = "cds"                         # trades #28-30 (single-name + index credit default swap)
    FX_FORWARD = "fx_forward"           # trade #27 (deliverable/NDF FX forward)
    XCCY_SWAP = "xccy_swap"             # trade #26 (cross-currency swap)


class SwapDirection(str, Enum):
    """Direction from the book's perspective on the fixed leg."""
    PAYER = "payer"        # pay fixed, receive float
    RECEIVER = "receiver"  # receive fixed, pay float


class OptionRight(str, Enum):
    CALL = "call"
    PUT = "put"


class ExerciseStyle(str, Enum):
    EUROPEAN = "european"
    BERMUDAN = "bermudan"
    AMERICAN = "american"


class SettlementType(str, Enum):
    PHYSICAL = "physical"
    CASH = "cash"


class ProtectionSide(str, Enum):
    """CDS protection direction from the book's perspective."""
    BUY = "buy"      # buy protection (long credit protection / short credit risk)
    SELL = "sell"    # sell protection (short protection / long credit risk)


class CdsKind(str, Enum):
    SINGLE_NAME = "single_name"
    INDEX = "index"


class FxDirection(str, Enum):
    """Book direction in USD vs the foreign currency."""
    LONG_USD = "long_usd"      # receive USD, pay foreign (buy USD fwd / receive-USD CCS leg)
    SHORT_USD = "short_usd"    # pay USD, receive foreign


class VolType(str, Enum):
    """Quoting convention for the volatility input."""
    NORMAL = "normal"        # Bachelier (bp vol) — SOFR-era market standard
    LOGNORMAL = "lognormal"  # Black


class LegType(str, Enum):
    FIXED = "fixed"
    OVERNIGHT = "overnight"  # compounded RFR (SOFR) leg
    IBOR = "ibor"            # term index leg (future use)


class DayCount(str, Enum):
    ACT_360 = "Actual/360"
    ACT_365F = "Actual/365Fixed"
    ACT_ACT_ISDA = "ActualActual.ISDA"
    THIRTY_360 = "30/360"
    ACT_ACT_BOND = "ActualActual.Bond"


class Frequency(str, Enum):
    ANNUAL = "Annual"
    SEMIANNUAL = "Semiannual"
    QUARTERLY = "Quarterly"
    MONTHLY = "Monthly"
    ONCE = "Once"


class BusinessDayConvention(str, Enum):
    FOLLOWING = "Following"
    MODIFIED_FOLLOWING = "ModifiedFollowing"
    PRECEDING = "Preceding"
    MODIFIED_PRECEDING = "ModifiedPreceding"
    UNADJUSTED = "Unadjusted"


class CalendarCode(str, Enum):
    """Holiday calendars (mapped to QuantLib calendars in the QL layer)."""
    US_GOVERNMENT_BOND = "US.GovernmentBond"
    US_SOFR = "US.SOFR"
    US_SETTLEMENT = "US.Settlement"
    TARGET = "TARGET"
    JAPAN = "Japan"
    TAIWAN = "Taiwan"


class CurvePurpose(str, Enum):
    """Role a curve plays for an instrument (multi-curve framework).

    For SOFR OIS the DISCOUNT and FORECAST curves resolve to the same SOFR curve
    object, but keeping the roles distinct keeps the design multi-curve-ready
    (Term SOFR basis, non-USD CSA, cross-currency) with no refactor.
    """
    DISCOUNT = "discount"
    FORECAST = "forecast"


class MarketDataKind(str, Enum):
    """Categories of market data an instrument can require."""
    OIS_CURVE = "ois_curve"            # par-OIS-bootstrapped yield curve
    TREASURY_CURVE = "treasury_curve"  # par-bootstrapped UST discount curve
    CREDIT_SPREAD = "credit_spread"    # issuer Z-spread over the risk-free curve
    EQUITY_MARKET = "equity_market"    # spot + dividend yield + vol surface for an underlier
    EQUITY_SPOT = "equity_spot"        # spot price (+ trailing div yield) for a cash-equity name
    CDS_SPREAD = "cds_spread"          # par CDS spread (+ recovery) for a reference entity / index
    FX = "fx"                          # FX spot (foreign per USD) + foreign discount rate
    NORMAL_VOL = "normal_vol"          # flat/surface normal (Bachelier) vol
    LOGNORMAL_VOL = "lognormal_vol"
    FIXING = "fixing"                  # historical index fixing
