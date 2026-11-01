"""Market-data domain objects.

Two halves:

1. *Requirements* — what a trade declares it needs (`MarketDataRequirement`,
   `MarketDataRequest`). Produced by the input layer; consumed by the loader.

2. *Resolved inputs* — the actual data handed back by the loader
   (`ParOISCurveData`, `FlatNormalVolData`), bundled in a `MarketDataSet` that the
   QuantLib layer turns into curves/vols.

Everything here is QuantLib-free and directly constructible in tests (mocking).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

from .conventions import IndexConvention
from .enums import CurvePurpose, MarketDataKind, VolType


# --------------------------------------------------------------------------- #
# Requirements (declared by trades, resolved by the loader)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketDataRequirement:
    """One atomic piece of market data an instrument needs.

    The (kind, purpose, currency, index_name, tenor) tuple is the resolution key
    the loader matches against, and the lookup key inside a MarketDataSet.
    """
    kind: MarketDataKind
    currency: str
    purpose: CurvePurpose | None = None   # DISCOUNT / FORECAST for curves
    index_name: str | None = None         # e.g. "USD-SOFR"
    tenor: str | None = None              # e.g. option/underlier tenor for a vol
    label: str | None = None              # optional human label

    @property
    def key(self) -> str:
        parts = [
            self.kind.value,
            self.currency,
            self.purpose.value if self.purpose else "-",
            self.index_name or "-",
            self.tenor or "-",
        ]
        return "|".join(parts)


@dataclass
class MarketDataRequest:
    """The full set of requirements for a single trade at a valuation date."""
    trade_id: str
    valuation_date: date
    requirements: list[MarketDataRequirement] = field(default_factory=list)

    def add(self, req: MarketDataRequirement) -> MarketDataRequest:
        self.requirements.append(req)
        return self

    def of_kind(self, kind: MarketDataKind) -> list[MarketDataRequirement]:
        return [r for r in self.requirements if r.kind == kind]


# --------------------------------------------------------------------------- #
# Resolved inputs (returned by the loader)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParOISCurveData:
    """Par-OIS quotes sufficient to bootstrap a single-currency OIS curve.

    tenors / par_rates are aligned: par_rates[i] is the at-market fixed rate of a
    spot-starting OIS of maturity tenors[i] (decimal, e.g. 0.0382 for 3.82%).
    """
    currency: str
    index: IndexConvention
    as_of_date: date
    tenors: tuple[str, ...]            # e.g. ("1Y","2Y","3Y","5Y","7Y","10Y")
    par_rates: tuple[float, ...]       # decimal
    source: str = "pensford_forward_curve"

    def __post_init__(self) -> None:
        if len(self.tenors) != len(self.par_rates):
            raise ValueError("tenors and par_rates length mismatch")
        if len(self.tenors) < 2:
            raise ValueError("need at least 2 par-OIS nodes to bootstrap")


@dataclass(frozen=True)
class TreasuryCurveData:
    """Par US-Treasury yields sufficient to bootstrap a UST discount curve.

    tenors / par_yields aligned: par_yields[i] is the par (coupon-equivalent)
    yield of the on-the-run Treasury at maturity tenors[i] (decimal).
    """
    currency: str
    as_of_date: date
    tenors: tuple[str, ...]
    par_yields: tuple[float, ...]
    source: str = "treasury_gov_par_yields"

    def __post_init__(self) -> None:
        if len(self.tenors) != len(self.par_yields):
            raise ValueError("tenors and par_yields length mismatch")
        if len(self.tenors) < 2:
            raise ValueError("need at least 2 par-yield nodes to bootstrap")


@dataclass(frozen=True)
class CreditSpreadData:
    """Issuer credit spread over the risk-free curve.

    `spread` is the headline continuous zero-spread (decimal, at the primary 5Y tenor)
    applied via a ZeroSpreadedTermStructure. `curve_points` optionally carries a **term
    structure** of Z-spreads as ascending `(tenor_years, spread)` pairs (A3): when it holds
    >= 2 tenors the corporate-bond pricer discounts on a date-dependent spreaded curve
    (SpreadedLinearZeroInterpolatedTermStructure) instead of one flat spread, so the
    issuer credit curve's shape is respected and the bond is consistent with the CDS term
    structure. Empty for issuers with only one clean tenor / a config fallback (they stay
    flat). The 5Y point, when present, equals `spread`. `from_cds` records whether it was
    derived from DTCC CDS data or fell back to a configured placeholder.
    """
    issuer: str
    currency: str
    spread: float                      # decimal, e.g. 0.0050 = 50bp
    as_of_date: date
    recovery: float = 0.40
    from_cds: bool = False
    source: str = "config-fallback"
    curve_points: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class EquityVolSurface:
    """Listed-option implied-vol grid for one underlier (skew + term).

    Holds aligned arrays of (days_to_expiry, strike, iv). `implied_vol` does a
    skew-aware lookup: interpolate across strike within each bracketing expiry,
    then interpolate across expiry in total variance. Pure numpy (QuantLib-free).
    """
    underlier: str
    days_to_expiry: tuple[int, ...]
    strikes: tuple[float, ...]
    ivs: tuple[float, ...]
    as_of_date: date

    def implied_vol(self, t_years: float, strike: float) -> float:
        days = np.asarray(self.days_to_expiry, dtype=float)
        strk = np.asarray(self.strikes, dtype=float)
        iv = np.asarray(self.ivs, dtype=float)
        target_days = max(t_years * 365.0, 1.0)

        uniq = np.sort(np.unique(days))
        # nearest expiries bracketing the target
        lo = uniq[uniq <= target_days].max() if (uniq <= target_days).any() else uniq.min()
        hi = uniq[uniq >= target_days].min() if (uniq >= target_days).any() else uniq.max()

        def smile_vol(day_val: float) -> float:
            m = days == day_val
            ks, vs = strk[m], iv[m]
            order = np.argsort(ks)
            return float(np.interp(strike, ks[order], vs[order]))

        v_lo, v_hi = smile_vol(lo), smile_vol(hi)
        if hi == lo:
            return v_lo
        # linear in total variance across expiry
        w = (target_days - lo) / (hi - lo)
        var = (1 - w) * v_lo ** 2 * lo + w * v_hi ** 2 * hi
        return float((var / target_days) ** 0.5)


@dataclass(frozen=True)
class EquityMarketData:
    """Resolved spot + dividend yield + vol surface for an option underlier.

    spot and dividend_yield are recovered from the option chain via put-call
    parity (ln-forward regression across expiries); the risk-free rate comes
    separately from the SOFR discount curve.
    """
    underlier: str
    currency: str
    spot: float
    dividend_yield: float
    as_of_date: date
    vol_surface: EquityVolSurface
    source: str = "option_chain_put_call_parity"
    # B1: projected discrete ex-dividend schedule (ex_date, per-share amount), ascending,
    # for dividend-paying underliers — empty for non-payers. The option pricer uses this
    # for American legs (correct early-exercise timing around ex-dates) while preserving the
    # chain-implied forward; the continuous `dividend_yield` remains the fallback.
    dividends: tuple[tuple[date, float], ...] = ()


@dataclass(frozen=True)
class FxData:
    """FX spot + foreign discount rate for a USD/foreign pair.

    `spot` is foreign units per 1 USD (e.g. 159.25 JPY/USD, 31.37 TWD/USD).
    `foreign_rate` is the flat money-market rate used to discount the foreign leg
    (JPY TONAR; TWD CBC policy rate). USD discounting comes from the SOFR curve.

    `xccy_basis_bp` (C2) is the cross-currency basis added to the foreign discount rate
    — the effective foreign curve is `foreign_rate + xccy_basis_bp/1e4`. It defaults to 0
    (no free TWD/xccy-basis feed) and is a documented hook: a licensed basis drops in via
    config with no code change. `effective_foreign_rate` is the rate the pricer uses.
    """
    foreign_ccy: str
    spot: float                        # foreign per USD
    foreign_rate: float                # decimal, flat
    as_of_date: date
    source: str = "fx_pipeline"
    xccy_basis_bp: float = 0.0

    @property
    def usd_per_foreign(self) -> float:
        return 1.0 / self.spot

    @property
    def effective_foreign_rate(self) -> float:
        """Foreign discount rate incl. the cross-currency basis (C2)."""
        return self.foreign_rate + self.xccy_basis_bp / 1e4


@dataclass(frozen=True)
class CdsSpreadData:
    """Par CDS spread (+ recovery) for a reference entity or index.

    `spread` is the running par spread in decimal (e.g. 0.0175 = 175bp) at the primary
    `tenor` (5Y) — retained for the flat-hazard path and the corp-bond spread lookup.
    `curve_points` optionally carries a **term structure** of par spreads as ascending
    `(tenor_years, spread)` pairs (A2): when it holds >= 2 tenors the CDS pricer
    bootstraps a piecewise-flat hazard curve consistent with the ISDA engine instead of
    a single flat hazard. It is empty for indices and for thin single names (which fall
    back to the flat calibration on `spread`). The 5Y point, when present, equals
    `spread`. `from_dtcc` records whether it came from the DTCC feed or a fallback.

    For an **index** (B4), `index_factor` is the outstanding notional fraction (1.0 for an
    on-the-run series with no defaulted constituents; < 1 once names default), applied to the
    traded notional, and `n_constituents` is the series size — reported so the index-level
    model is explicit. Both default to 1.0 / None for single names.
    """
    reference: str
    currency: str
    spread: float
    recovery: float
    as_of_date: date
    tenor: str = "5Y"
    from_dtcc: bool = False
    source: str = "config-fallback"
    curve_points: tuple[tuple[float, float], ...] = ()
    index_factor: float = 1.0
    n_constituents: int | None = None


@dataclass(frozen=True)
class EquitySpotData:
    """Spot price (+ trailing dividend yield) for a cash-equity / ETF position."""
    ticker: str
    currency: str
    spot: float
    as_of_date: date
    dividend_yield: float = 0.0
    source: str = "prices_daily"


@dataclass(frozen=True)
class FlatNormalVolData:
    """A (Bachelier) normal volatility, MOVE-anchored.

    `vol` is the headline level. `term_structure` (C1) optionally carries an expiry-dependent
    normal-vol curve as ascending `(expiry_years, vol)` pairs, so a swaption/cap gets the vol at
    its own expiry and the Bermudan co-terminal calibration is fed expiry-specific targets
    (which is what identifies the Hull-White mean reversion) instead of one flat number. The
    curve is still flat across strike — the smile/cube needs licensed data. Empty term_structure
    => `vol_at` returns the flat `vol` (backward compatible).
    """
    currency: str
    vol: float                         # normal vol in decimal, e.g. 0.0090 = 90bp
    as_of_date: date
    vol_type: VolType = VolType.NORMAL
    anchor: str = "MOVE-derived (config)"
    tenor: str | None = None
    term_structure: tuple[tuple[float, float], ...] = ()

    def vol_at(self, expiry_years: float) -> float:
        """Normal vol at `expiry_years` — linear interpolation across the term structure,
        flat-extrapolated at the ends; the flat `vol` when no term structure is present."""
        ts = self.term_structure
        if not ts:
            return self.vol
        if expiry_years <= ts[0][0]:
            return ts[0][1]
        if expiry_years >= ts[-1][0]:
            return ts[-1][1]
        for i in range(len(ts) - 1):
            x0, v0 = ts[i]
            x1, v1 = ts[i + 1]
            if x0 <= expiry_years <= x1:
                w = (expiry_years - x0) / (x1 - x0)
                return v0 + w * (v1 - v0)
        return self.vol


# --------------------------------------------------------------------------- #
# Resolved bundle handed to the QuantLib layer
# --------------------------------------------------------------------------- #
@dataclass
class MarketDataSet:
    """Container of resolved market data for one trade, keyed by requirement.

    Carries `warnings` (staleness etc.) and `as_of_dates` for diagnostics that the
    pricer copies into the PricingResult.
    """
    valuation_date: date
    items: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    as_of_dates: dict[str, str] = field(default_factory=dict)
    snapshot_id: str | None = None  # content-addressed market-data snapshot (P0b)

    # -- population ---------------------------------------------------------- #
    def put(self, req: MarketDataRequirement, data: object, as_of: date | None = None) -> None:
        self.items[req.key] = data
        if as_of is not None:
            self.as_of_dates[req.key] = as_of.isoformat()

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    # -- typed accessors ----------------------------------------------------- #
    def get(self, req: MarketDataRequirement) -> object:
        if req.key not in self.items:
            raise KeyError(f"Market data not resolved for requirement: {req.key}")
        return self.items[req.key]

    def curve(self, currency: str, purpose: CurvePurpose, index_name: str) -> ParOISCurveData:
        req = MarketDataRequirement(
            kind=MarketDataKind.OIS_CURVE, currency=currency,
            purpose=purpose, index_name=index_name,
        )
        return self.get(req)  # type: ignore[return-value]

    def normal_vol(self, currency: str, tenor: str | None = None) -> FlatNormalVolData:
        req = MarketDataRequirement(
            kind=MarketDataKind.NORMAL_VOL, currency=currency, tenor=tenor,
        )
        return self.get(req)  # type: ignore[return-value]

    def treasury_curve(self, currency: str) -> TreasuryCurveData:
        req = MarketDataRequirement(
            kind=MarketDataKind.TREASURY_CURVE, currency=currency,
            purpose=CurvePurpose.DISCOUNT,
        )
        return self.get(req)  # type: ignore[return-value]

    def credit_spread(self, currency: str, issuer: str) -> CreditSpreadData:
        req = MarketDataRequirement(
            kind=MarketDataKind.CREDIT_SPREAD, currency=currency, index_name=issuer,
        )
        return self.get(req)  # type: ignore[return-value]

    def equity_market(self, currency: str, underlier: str) -> EquityMarketData:
        req = MarketDataRequirement(
            kind=MarketDataKind.EQUITY_MARKET, currency=currency, index_name=underlier,
        )
        return self.get(req)  # type: ignore[return-value]

    def equity_spot(self, currency: str, ticker: str) -> EquitySpotData:
        req = MarketDataRequirement(
            kind=MarketDataKind.EQUITY_SPOT, currency=currency, index_name=ticker,
        )
        return self.get(req)  # type: ignore[return-value]

    def cds_spread(self, currency: str, reference: str) -> CdsSpreadData:
        req = MarketDataRequirement(
            kind=MarketDataKind.CDS_SPREAD, currency=currency, index_name=reference,
        )
        return self.get(req)  # type: ignore[return-value]

    def fx(self, foreign_ccy: str) -> FxData:
        req = MarketDataRequirement(
            kind=MarketDataKind.FX, currency="USD", index_name=foreign_ccy,
        )
        return self.get(req)  # type: ignore[return-value]
