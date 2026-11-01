"""Option-implied credit spread via the Merton structural model (market-intelligence B3, signal 6).

Merton (1974): a firm's equity is a call option on its assets ``V`` struck at its debt ``D``.
Given the two market observables — equity market cap ``E`` and equity volatility ``sigma_E`` (here the
option-implied ATM vol) — plus the debt face and horizon, the asset value and asset vol are
recovered from the pair of equations

    E   = V*N(d1) - D*e^{-rT}*N(d2)                       (equity = call on assets)
    sigma_E = (V/E)*N(d1)*sigma_V                                  (Ito on that call)

and the risk-neutral credit spread follows from the risky-debt value
``B = V*N(-d1) + D*e^{-rT}*N(d2)`` as ``s = -(1/T)*ln(B / D*e^{-rT})``.

This is an INDICATOR — a financial-health early-warning and a cross-check on the traded CDS
(design §3.8: "report it as an indicator with a stated calibration, never as a spread feeding
PV"). The DTCC single-name CDS feed remains the pricing input; the Merton spread is compared to it,
it does not replace it. numpy/math only — the two-equation solve is a KMV fixed-point, no scipy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

__all__ = ["MertonResult", "build_credit_table", "creditgrades_spread", "merton_implied_spread"]


def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def creditgrades_spread(
    spot: float,
    equity_vol: float,
    debt_per_share: float,
    *,
    horizon_years: float = 1.0,
    recovery: float = 0.50,
    lbar: float = 0.50,
    lam: float = 0.30,
) -> float | None:
    """CreditGrades (Finger et al. 2002) option-implied credit spread, in basis points.

    Where the basic Merton model gives ~0 short-horizon spreads for investment-grade firms (the
    "credit-spread puzzle" — distance-to-default is large, so ``N(-d2)`` is negligible),
    CreditGrades restores realistic non-zero spreads by making the default barrier itself random
    (lognormal, mean ``lbar``, vol ``lam``): default can happen "by surprise", so even a safe firm
    prices a spread. This is the model the design pairs with Merton for exactly that reason.

    Survival probability ``P(t) = N(-A_t/2 + ln d / A_t) - d*N(-A_t/2 - ln d / A_t)`` with
    ``d = (S + L*Dps)/(L*Dps)*e^{lambda^2}`` and ``A_t^2 = sigma^2t + lambda^2``; the spread follows from the
    credit triangle ``s = -(1-R)*ln P(t) / t``. Asset vol ``sigma`` is the equity vol de-levered by
    ``S/(S + L*Dps)``. Returns ``None`` for degenerate inputs.
    """
    s, sig_e, dps, t, r_rec = spot, equity_vol, debt_per_share, horizon_years, recovery
    if s <= 0 or sig_e <= 0 or dps <= 0 or t <= 0 or not (0.0 < lbar < 1.0) or lam <= 0:
        return None
    barrier = lbar * dps
    sigma = sig_e * s / (s + barrier)                    # equity vol -> asset vol
    d = (s + barrier) / barrier * math.exp(lam**2)
    a_t = math.sqrt(sigma**2 * t + lam**2)
    ln_d = math.log(d)
    surv = _n(-a_t / 2 + ln_d / a_t) - d * _n(-a_t / 2 - ln_d / a_t)
    if not (0.0 < surv < 1.0):
        return 0.0 if surv >= 1.0 else None
    return -(1.0 - r_rec) * math.log(surv) / t * 1e4


@dataclass
class MertonResult:
    asset_value: float
    asset_vol: float
    distance_to_default: float     # d2 — standard deviations of asset value to the default point
    default_prob: float            # N(-d2), risk-neutral
    spread_bp: float               # Merton credit spread in basis points
    converged: bool


def merton_implied_spread(
    equity_value: float,
    equity_vol: float,
    debt: float,
    *,
    horizon_years: float = 1.0,
    risk_free: float = 0.04,
    max_iter: int = 100,
    tol: float = 1e-8,
) -> MertonResult | None:
    """Solve the Merton model for the option-implied credit spread.

    ``equity_value`` = market cap (spot x shares), ``equity_vol`` = option-implied equity vol,
    ``debt`` = face value of debt. Returns ``None`` for degenerate inputs (non-positive equity/
    vol, or zero debt — a firm with no debt has no structural credit spread).
    """
    e, sig_e, d, t, r = equity_value, equity_vol, debt, horizon_years, risk_free
    if e <= 0 or sig_e <= 0 or d <= 0 or t <= 0:
        return None

    d_disc = d * math.exp(-r * t)
    v = e + d_disc                       # asset value guess: equity + PV(debt)
    sig_v = max(sig_e * e / v, 1e-4)     # asset vol guess: de-levered equity vol
    converged = False
    for _ in range(max_iter):
        vsqrt = sig_v * math.sqrt(t)
        d1 = (math.log(v / d) + (r + 0.5 * sig_v**2) * t) / vsqrt
        d2 = d1 - vsqrt
        nd1 = _n(d1)
        v_new = (e + d_disc * _n(d2)) / nd1 if nd1 > 1e-12 else v
        sig_v_new = sig_e * e / (v_new * nd1) if (v_new * nd1) > 1e-12 else sig_v
        if abs(v_new - v) < tol * v and abs(sig_v_new - sig_v) < tol * max(sig_v, 1e-6):
            v, sig_v = v_new, sig_v_new
            converged = True
            break
        v, sig_v = v_new, sig_v_new

    vsqrt = sig_v * math.sqrt(t)
    d1 = (math.log(v / d) + (r + 0.5 * sig_v**2) * t) / vsqrt
    d2 = d1 - vsqrt
    risky_debt = v * _n(-d1) + d_disc * _n(d2)
    # Guard: risky debt must be positive and below the risk-free value for a real spread.
    spread = -math.log(risky_debt / d_disc) / t if (0 < risky_debt <= d_disc) else 0.0
    return MertonResult(
        asset_value=v, asset_vol=sig_v, distance_to_default=d2,
        default_prob=_n(-d2), spread_bp=spread * 1e4, converged=converged,
    )


def build_credit_table(
    signals: pd.DataFrame,
    fundamentals: dict,
    *,
    vol_col_primary: str = "atm_iv_90d",
    vol_col_fallback: str = "atm_iv_30d",
    spot_col: str = "rnd_spot",
) -> pd.DataFrame:
    """Per-(issuer, snapshot) Merton spread + CDS cross-check, for issuers in ``fundamentals``.

    Reads the recovered spot and option-implied vol straight off the B1/B2 signals frame, so it
    consumes what the earlier stages already produced. Issuers with no fundamentals entry, no
    recovered spot, or no usable vol are skipped (not every chain name is a credit name).
    """
    issuers = fundamentals.get("issuers", {})
    t = float(fundamentals.get("horizon_years", 1.0))
    r = float(fundamentals.get("risk_free_rate", 0.04))
    recovery = float(fundamentals.get("recovery_rate", 0.40))
    rows: list[dict[str, object]] = []
    for _i, s in signals.iterrows():
        name = str(s["underlying"])
        f = issuers.get(name)
        if f is None:
            continue
        spot = s.get(spot_col)
        vol = s.get(vol_col_primary)
        if pd.isna(vol):
            vol = s.get(vol_col_fallback)
        if pd.isna(spot) or pd.isna(vol):
            continue

        debt = float(f["total_debt_usd"])
        shares = float(f["shares_outstanding"])
        equity_value = float(spot) * shares
        merton = merton_implied_spread(equity_value, float(vol), debt, horizon_years=t, risk_free=r)
        # CreditGrades is the primary spread (non-degenerate for IG, see its docstring); Merton
        # supplies the complementary distance-to-default / structural default probability.
        cg = creditgrades_spread(float(spot), float(vol), debt / shares,
                                 horizon_years=t, recovery=recovery)
        cds = f.get("cds_reference_bp")
        rows.append({
            "snapshot_date": s["snapshot_date"],
            "underlying": name,
            "equity_vol": round(float(vol), 6),
            "spot": round(float(spot), 4),
            "equity_value_usd": round(equity_value, 0),
            "debt_usd": debt,
            "leverage": round(debt / (equity_value + debt), 4),
            "cg_spread_bp": (round(cg, 2) if cg is not None else None),
            "merton_spread_bp": (round(merton.spread_bp, 2) if merton is not None else None),
            "merton_dd": (round(merton.distance_to_default, 4) if merton is not None else None),
            "merton_pd": (round(merton.default_prob, 6) if merton is not None else None),
            "cds_reference_bp": (float(cds) if cds is not None else None),
            "basis_bp": (round(cg - float(cds), 2) if (cg is not None and cds is not None) else None),
        })
    cols = ["snapshot_date", "underlying", "equity_vol", "spot", "equity_value_usd", "debt_usd",
            "leverage", "cg_spread_bp", "merton_spread_bp", "merton_dd", "merton_pd",
            "cds_reference_bp", "basis_bp"]
    return pd.DataFrame(rows, columns=cols).sort_values(["underlying", "snapshot_date"])
