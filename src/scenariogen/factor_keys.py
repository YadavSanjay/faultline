"""Canonical risk-factor taxonomy shared across scenariogen (S0/S1).

A *factor key* is a stable string that names one column of the aligned factor-move
panel (`FactorHistory.aligned_panel`), one coordinate of a covariance matrix, and one
entry of a materialized scenario vector. Keeping one taxonomy means Σ, the conditional
propagation, and the bump transforms all speak the same language.

All factor values are carried in **canonical native units** — the same units the bump
engine consumes — so a materialized vector maps to transforms with no rescaling:

    ir:DGS2/5/10/30   decimal rate change (curve-shape control nodes)
    eq:<TICKER>       decimal return (e.g. -0.25 = -25%)
    eqvol             decimal additive IV shift (level; e.g. +0.20 = +20 vol points)
    eqvol_term        decimal vol term-slope tilt (VIX3M-VIX driven, GAP 4)
    eqvol_skew        decimal put-skew tilt per unit log-moneyness (SKEW driven, GAP 4)
    ratesvol          relative normal-vol shift (level; e.g. +0.30 = +30%)
    ratesvol_term     decimal normal-vol term-slope tilt (per-expiry, GAP 6; driver/overlay)
    fx:<CCY>          decimal return of the USD/<CCY> pair (foreign per USD)
    fxrate:<CCY>      decimal additive shift of the foreign discount rate (FX rho, GAP 5)
    credit            decimal additive spread change (e.g. +0.0100 = +100bp)
    style:<FACTOR>    decimal daily return of a Ken French style factor (F4; e.g. style:MOM).
                      A DRIVER-ONLY factor: it lives in Σ so a "momentum crash" propagates to the
                      equity legs via conditional covariance, but it has no bump transform, so it
                      is never repriced directly — its P&L is entirely the equity moves it implies.
    basis:<HEDGE>     decimal daily return of a hedge/basis spread (F3; e.g. basis:SMH_SEMI =
                      SMH return - single-name-semis return). Driver-only like style: it propagates
                      a dispersion across the equity legs (the hedge failing), no bump of its own.
    real:<SERIES>     decimal daily change of a TIPS real yield (F6; e.g. real:DFII10). Driver-only.
    be:<SERIES>       decimal daily change of a breakeven-inflation series (F6; e.g. be:T10YIE).
    tp:<SERIES>       decimal daily change of an ACM term-premium series (F6; e.g. tp:ACMTP10).
                      The three rate-decomposition drivers split a nominal move into real vs
                      breakeven vs term-premium: authored as a driver, real:DFII10 propagates the
                      2022-style "tech AND long bonds fall together" co-move that a nominal-only
                      factor set cannot express. Driver-only like style/basis: no bump transform.
"""

from __future__ import annotations

# IR curve-shape control tenors -> tenor in years (must match factor_history; GAP 3 grid).
IR_TENOR_YEARS: dict[str, float] = {
    "DGS1MO": 1.0 / 12.0, "DGS3MO": 0.25, "DGS6MO": 0.5, "DGS1": 1.0,
    "DGS2": 2.0, "DGS3": 3.0, "DGS5": 5.0, "DGS7": 7.0,
    "DGS10": 10.0, "DGS20": 20.0, "DGS30": 30.0,
}

# The systematic-equity proxy: its move is the default applied to option underliers
# that have no own spot history (AAPL/MSFT/META/TSLA/QQQ/SPX).
SYSTEMATIC_EQUITY = "XLK"

# Limited-liability floor for multiplicative spot returns (equity, FX): a long position
# can't lose more than ~100%. The linear conditional-covariance propagation can imply
# moves below -100% for high-beta names under a large driver shock; clamping keeps the
# applied spot strictly positive (scale_* uses spot*(1+rel), so rel<=-1 -> non-positive
# price) and the scenario economically valid. -0.95 = a near-total but non-zero wipeout.
RETURN_FLOOR = -0.95

IR_PREFIX = "ir:"
EQUITY_PREFIX = "eq:"
FX_PREFIX = "fx:"
FX_RATE_PREFIX = "fxrate:"
EQUITY_VOL_KEY = "eqvol"
EQUITY_VOL_TERM_KEY = "eqvol_term"     # vol term-slope tilt (GAP 4)
EQUITY_VOL_SKEW_KEY = "eqvol_skew"     # put-skew tilt (GAP 4)
RATES_VOL_KEY = "ratesvol"
RATES_VOL_TERM_KEY = "ratesvol_term"   # normal-vol term-slope tilt (GAP 6; driver/overlay only)
CREDIT_KEY = "credit"
ISSUER_CREDIT_PREFIX = "credit:"           # per-issuer credit spread (issuer-credit-factors)
STYLE_PREFIX = "style:"                 # Ken French style factors (F4); driver-only, no bump
STYLE_MOM_KEY = "style:MOM"            # momentum factor -> the "momentum crash" driver
BASIS_PREFIX = "basis:"                 # hedge/basis spreads (F3)
BASIS_ETF_KEY = "basis:SMH_SEMI"       # SMH-vs-single-name-semis dispersion (trade #6); driver-only (pct)
BASIS_SWAP_SPREAD_KEY = "basis:SWAP_SPREAD"   # Treasury - SOFR OIS; direct Treasury-curve bump (bp)
BASIS_CDS_BOND_KEY = "basis:CDS_BOND"         # CDS par - bond Z-spread; direct CDS-spread bump (bp)
# The two spread bases reprice via a bump (bp units); the ETF basis propagates via Σ (pct units).
BP_BASIS_KEYS = frozenset({BASIS_SWAP_SPREAD_KEY, BASIS_CDS_BOND_KEY})

# Rate-decomposition drivers (F6): split a nominal move into real / breakeven / term-premium.
# Driver-only (like style/basis) — they live in Σ and propagate via conditional covariance, but
# have no bump transform, so the full-reval panel never carries them.
REAL_PREFIX = "real:"
BREAKEVEN_PREFIX = "be:"
TERMP_PREFIX = "tp:"
REAL_10Y_KEY = "real:DFII10"          # 10Y TIPS real yield
BREAKEVEN_10Y_KEY = "be:T10YIE"       # 10Y breakeven inflation
TERM_PREMIUM_10Y_KEY = "tp:ACMTP10"   # ACM 10Y term premium


def is_ir(key: str) -> bool:
    return key.startswith(IR_PREFIX)


def is_style(key: str) -> bool:
    return key.startswith(STYLE_PREFIX)


def is_basis(key: str) -> bool:
    return key.startswith(BASIS_PREFIX)


def is_rates_decomp(key: str) -> bool:
    """A rate-decomposition driver (F6): real / breakeven / term-premium. Driver-only, no bump."""
    return key.startswith((REAL_PREFIX, BREAKEVEN_PREFIX, TERMP_PREFIX))


def is_equity(key: str) -> bool:
    return key.startswith(EQUITY_PREFIX)


def is_fx(key: str) -> bool:
    return key.startswith(FX_PREFIX)


def ir_tenor(key: str) -> str:
    """``ir:DGS10`` -> ``DGS10``."""
    return key[len(IR_PREFIX):]


def equity_name(key: str) -> str:
    """``eq:NVDA`` -> ``NVDA``."""
    return key[len(EQUITY_PREFIX):]


def fx_ccy(key: str) -> str:
    """``fx:TWD`` -> ``TWD``."""
    return key[len(FX_PREFIX):]


def canonical_fx_key(key: str) -> str:
    """Normalize an fx factor key to canonical ``fx:<CCY>`` (USD/<CCY>, foreign per USD).

    MacroRAG's shock schema names FX legs by their USD-base pair, e.g. ``fx:USDTWD`` /
    ``fx:USDJPY``, but the canonical taxonomy carries only the foreign leg (``fx:TWD`` /
    ``fx:JPY``) because every pair is quoted against USD. A 6-letter pair code containing
    ``USD`` collapses to its non-USD leg; the currency is upper-cased; anything else is
    returned unchanged. Orientation is preserved — MacroRAG's ``USD<CCY>`` matches the
    canonical ``USD/<CCY>`` convention, so the move sign is not flipped. Non-fx keys pass
    through untouched.
    """
    if not is_fx(key):
        return key
    ccy = fx_ccy(key).strip().upper()
    if len(ccy) == 6 and ccy.count("USD") == 1:
        other = ccy.replace("USD", "", 1)
        if len(other) == 3:
            ccy = other
    return FX_PREFIX + ccy


def is_fx_rate(key: str) -> bool:
    return key.startswith(FX_RATE_PREFIX)


def fx_rate_ccy(key: str) -> str:
    """``fxrate:JPY`` -> ``JPY``."""
    return key[len(FX_RATE_PREFIX):]


def issuer_credit_key(issuer: str) -> str:
    """Return the per-issuer credit factor key ``credit:<ISSUER>`` (issuer upper-cased).

    Args:
        issuer: Issuer name, e.g. ``ORCL``.

    Returns:
        ``"credit:" + issuer.upper()``.
    """
    return ISSUER_CREDIT_PREFIX + issuer.upper()


def is_issuer_credit(key: str) -> bool:
    """Return whether ``key`` is a per-issuer credit key: ``credit:`` followed by a non-empty name.

    Args:
        key: A factor key. The plain ``credit`` key is not an issuer key.

    Returns:
        True exactly for ``credit:<name>`` with a non-empty name.
    """
    return key.startswith(ISSUER_CREDIT_PREFIX) and len(key) > len(ISSUER_CREDIT_PREFIX)


def issuer_credit_name(key: str) -> str:
    """Return the issuer name of a per-issuer credit key, e.g. ``credit:ORCL`` -> ``ORCL``.

    Args:
        key: A per-issuer credit key.

    Returns:
        The name after ``credit:``.

    Raises:
        ValueError: If ``key`` is not a per-issuer credit key.
    """
    if not is_issuer_credit(key):
        raise ValueError(f"not a per-issuer credit key: {key!r}")
    return key[len(ISSUER_CREDIT_PREFIX):]
