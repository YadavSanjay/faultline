"""Risk-neutral density and option-implied tail probabilities (market-intelligence B2, signal 5).

Breeden-Litzenberger: the risk-neutral density of the underlier at expiry T is proportional to the
second strike-derivative of the call price, ``f(K) = e^{rT} d^2C/dK^2``; the CDF is
``P(S_T <= K) = 1 + e^{rT} dC/dK``. Applied naively to raw quoted prices this is garbage — a second
derivative of sparse, noisy, discretely-quoted prices amplifies every wiggle (design §3.8, the one
item with real numerical risk). The standard mitigation, implemented here:

  1. Recover the forward ``F`` and discount factor ``D`` per expiry from put-call parity by
     regressing ``C - P`` on strike (``C - P = D*(F - K)`` -> slope ``-D``, intercept ``D*F``): no
     assumed rate, and it uses every liquid pair rather than one ATM point.
  2. Fit a SMOOTH implied-vol smile — a low-order polynomial in log-moneyness ``k = ln(K/F)`` over a
     moneyness band — so the reconstructed price surface is analytic and its curvature is stable.
  3. Reprice a DENSE strike grid with Black-Scholes at the fitted vol (in the forward measure, so the
     discount factor cancels and ``f`` integrates to 1), then finite-difference once for the CDF and
     twice for the density.
  4. Clip the density at zero and renormalize to integrate to 1 (a fitted smile can still dip
     slightly negative in the wings); the caller DQ-gates the result.

Everything is numpy — no scipy dependency. The normal CDF uses ``math.erf`` vectorized over the grid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = ["RndResult", "atm_expected_move", "recover_forward_curve", "risk_neutral_density"]

_NORM_CDF = np.vectorize(lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0))))


@dataclass
class RndResult:
    """One expiry's risk-neutral density plus the tail probabilities read off its CDF."""

    underlying: str
    snapshot_date: str
    dte: int
    forward: float
    spot: float
    strikes: np.ndarray          # dense grid
    pdf: np.ndarray              # risk-neutral density (integrates to ~1)
    cdf: np.ndarray              # P(S_T <= K), monotone 0..1
    tail: dict[str, float]       # prob_down_10/20/30, prob_up_10/20 (vs SPOT)

    def curve_frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "underlying": self.underlying,
            "snapshot_date": self.snapshot_date,
            "dte": self.dte,
            "strike": self.strikes,
            "pdf": self.pdf,
            "cdf": self.cdf,
        })


def _bs_forward_call(f: float, k: np.ndarray, t: float, sigma: np.ndarray) -> np.ndarray:
    """Undiscounted (forward-measure) Black-Scholes call value E[(S_T - K)+], vectorized over K."""
    sig = np.maximum(sigma, 1e-6)
    vsqrt = sig * math.sqrt(t)
    d1 = (np.log(f / k) + 0.5 * vsqrt**2) / vsqrt
    d2 = d1 - vsqrt
    return np.asarray(f * _NORM_CDF(d1) - k * _NORM_CDF(d2), dtype=float)


def _pairs(sub: pd.DataFrame) -> pd.DataFrame:
    """Median mid per (strike, side) -> a frame with columns strike, call, put for common strikes."""
    calls = sub[(sub["put_call"] == "call") & (sub["mid"] > 0)].groupby("strike")["mid"].median()
    puts = sub[(sub["put_call"] == "put") & (sub["mid"] > 0)].groupby("strike")["mid"].median()
    common = sorted(set(calls.index) & set(puts.index))
    return pd.DataFrame({"strike": common,
                         "call": [calls[k] for k in common],
                         "put": [puts[k] for k in common]})


def _forward_and_discount(sub: pd.DataFrame, min_pairs: int) -> tuple[float, float] | None:
    """Recover (forward, discount factor) for one expiry via the put-call-parity regression."""
    p = _pairs(sub)
    if len(p) < min_pairs:
        return None
    y = (p["call"] - p["put"]).to_numpy()
    x = p["strike"].to_numpy()
    slope, intercept = np.polyfit(x, y, 1)     # y = D*F + (-D)*x
    disc = -slope
    # A sane discount factor over <~2y. The upper bound is >1 on purpose: delayed mid quotes are
    # discrete and slightly noisy, so the parity slope routinely implies disc a touch above 1
    # (e.g. ~1.001 for QQQ) — rejecting those drops otherwise-healthy chains. F is barely affected.
    if not (0.8 <= disc <= 1.03):
        return None
    return float(intercept / disc), float(disc)


def recover_forward_curve(
    chain_1snap: pd.DataFrame, min_pairs: int = 5, min_t: float = 0.01, max_t: float = 2.0,
) -> tuple[float | None, dict[int, tuple[float, float]]]:
    """Spot (from ln F vs T) and a {dte: (forward, T)} map, both from put-call parity.

    Spot is recovered as the intercept of ``ln F(T)`` regressed on ``T`` across expiries (the same
    method the pricer's equity adapter uses), so tail probabilities can be quoted versus spot.
    Returns ``(None, {})`` if too few expiries carry a clean forward.
    """
    fwds: dict[int, tuple[float, float]] = {}
    for dte, g in chain_1snap.groupby("days_to_expiry"):
        t = int(dte) / 365.0
        if not (min_t <= t <= max_t):
            continue
        fd = _forward_and_discount(g, min_pairs)
        if fd is not None and fd[0] > 0:
            fwds[int(dte)] = (fd[0], t)
    if len(fwds) < 2:
        spot = next(iter(fwds.values()))[0] if fwds else None
        return spot, fwds
    ts = np.array([t for _f, t in fwds.values()])
    lnf = np.log(np.array([f for f, _t in fwds.values()]))
    _slope, intercept = np.polyfit(ts, lnf, 1)
    return float(np.exp(intercept)), fwds


def atm_expected_move(
    chain_1snap: pd.DataFrame, fwds: dict[int, tuple[float, float]], target_dte: int = 0,
) -> tuple[float, int] | None:
    """Signal 3 — the market's expected move to the front expiry: ATM straddle / forward.

    ``(C_atm + P_atm) / F`` at the strike nearest the forward, for the expiry nearest
    ``target_dte`` (0 = the shortest-dated, i.e. the classic front-expiry expected move — the
    number desks quote as "the options are pricing a +/-X% move"). Returns ``(move, dte)`` or None.

    This is the generic expected move; pinning it to the expiry that *straddles the next earnings
    date* awaits the earnings calendar that arrives with the 8-K work in P3.
    """
    if not fwds:
        return None
    dte = min(fwds, key=lambda d: abs(d - target_dte))
    f, _t = fwds[dte]
    g = chain_1snap[(chain_1snap["days_to_expiry"] == dte) & (chain_1snap["mid"] > 0)]
    calls = g[g["put_call"] == "call"].groupby("strike")["mid"].median()
    puts = g[g["put_call"] == "put"].groupby("strike")["mid"].median()
    common = sorted(set(calls.index) & set(puts.index))
    if not common:
        return None
    k_atm = min(common, key=lambda k: abs(k - f))
    straddle = float(calls[k_atm] + puts[k_atm])
    if f <= 0 or straddle <= 0:
        return None
    return straddle / f, int(dte)


def _smile_points(sub: pd.DataFrame, f: float, min_iv: float, max_iv: float,
                  band: float) -> tuple[np.ndarray, np.ndarray] | None:
    """OTM-preferred IV points as (log-moneyness k, iv) within |k| <= band for one expiry."""
    clean = sub[(sub["iv"] > min_iv) & (sub["iv"] < max_iv) & (sub["mid"] > 0)].copy()
    if clean.empty:
        return None
    # Prefer the OTM wing on each side: puts below the forward, calls above.
    otm = clean[((clean["put_call"] == "put") & (clean["strike"] <= f))
                | ((clean["put_call"] == "call") & (clean["strike"] >= f))]
    use = otm if not otm.empty else clean
    iv = use.groupby("strike")["iv"].median()
    k = np.log(iv.index.to_numpy(dtype=float) / f)
    mask = np.abs(k) <= band
    if mask.sum() < 4:
        return None
    return k[mask], iv.to_numpy()[mask]


def risk_neutral_density(
    chain_1snap: pd.DataFrame,
    underlying: str,
    snapshot_date: str,
    target_dte: int,
    *,
    spot: float | None = None,
    min_iv: float = 0.03,
    max_iv: float = 2.5,
    band: float = 0.6,
    smile_deg: int = 2,
    grid_n: int = 400,
    min_pairs: int = 5,
) -> RndResult | None:
    """Risk-neutral density + tail probabilities at the expiry nearest ``target_dte``.

    ``spot`` (from :func:`recover_forward_curve`) anchors the tail targets to spot; if ``None`` the
    forward is used instead. Returns ``None`` when the expiry has too thin a smile to fit safely —
    a garbage density is worse than none (design §3.8), so the bar to emit is deliberate.
    """
    fwd_spot, fwds = recover_forward_curve(chain_1snap, min_pairs=min_pairs)
    if not fwds:
        return None
    dte = min(fwds, key=lambda d: abs(d - target_dte))
    f, t = fwds[dte]
    anchor = float(spot if spot is not None else (fwd_spot if fwd_spot is not None else f))

    g = chain_1snap[chain_1snap["days_to_expiry"] == dte]
    pts = _smile_points(g, f, min_iv, max_iv, band)
    if pts is None:
        return None
    k_obs, iv_obs = pts
    deg = min(smile_deg, len(k_obs) - 1)
    coeffs = np.polyfit(k_obs, iv_obs, deg)

    # Dense strike grid across the fitted band; reprice and Breeden-Litzenberger.
    k_grid = np.linspace(k_obs.min(), k_obs.max(), grid_n)
    strikes = f * np.exp(k_grid)
    sigma = np.clip(np.polyval(coeffs, k_grid), min_iv, max_iv)
    call = _bs_forward_call(f, strikes, t, sigma)

    dk = np.gradient(strikes)
    dcall = np.gradient(call, strikes)
    cdf = np.clip(1.0 + dcall, 0.0, 1.0)                 # P(S_T <= K) in the forward measure
    cdf = np.maximum.accumulate(cdf)                      # enforce monotonicity
    pdf = np.gradient(dcall, strikes)                     # d2C/dK2
    pdf = np.clip(pdf, 0.0, None)
    area = float(np.sum(pdf * dk))
    if area <= 0:
        return None
    pdf = pdf / area                                     # renormalize to integrate to 1

    tail = _tail_probs(strikes, cdf, anchor)
    return RndResult(underlying, snapshot_date, int(dte), f, anchor, strikes, pdf, cdf, tail)


def _tail_probs(strikes: np.ndarray, cdf: np.ndarray, anchor: float) -> dict[str, float]:
    """P(S_T <= anchor*(1+x)) for downside and upside moves, read off the CDF by interpolation."""
    out: dict[str, float] = {}
    for tag, mv in (("prob_down_10", -0.10), ("prob_down_20", -0.20), ("prob_down_30", -0.30)):
        out[tag] = float(np.interp(anchor * (1 + mv), strikes, cdf))
    for tag, mv in (("prob_up_10", 0.10), ("prob_up_20", 0.20)):
        out[tag] = float(1.0 - np.interp(anchor * (1 + mv), strikes, cdf))
    return out
