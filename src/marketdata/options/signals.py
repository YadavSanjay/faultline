"""Option-implied signals from a single CBOE chain snapshot (market-intelligence B1).

Pure pandas/numpy derivation — no QuantLib, no network. Given ONE underlying's chain at ONE
`snapshot_date` (the columns the CBOE downloader writes: `days_to_expiry`, `put_call`, `strike`,
`mid`, `iv`, `delta`, `open_interest`, `volume`, ...) plus an optional daily close history for the
same name, `compute_signals` returns a flat dict of point-in-time signals:

  1. ATM implied-vol level, interpolated to a target tenor (per-name vol level — closes the GAP-4
     residual for the names with no published CBOE vol index).
  2. IV term structure — front vs back ATM IV, slope, and an inversion flag (front > back = the
     market pricing near-term stress).
  4. 25-delta skew — ``IV(25d put) - IV(25d call)`` at two tenors (crash-risk pricing per name).
  7. Variance risk premium — ATM IV minus trailing realized vol (cushion vs complacency).
  8. Positioning — put/call open-interest and volume ratios, and open-interest concentration.

Signals 3 (earnings implied move) and 5 (risk-neutral density / tail probability) are the
numerically fiddly two and land in B2; signal 6 (Merton/CreditGrades) needs ARag leverage and
lands in B3. Every signal here is point-in-time — computed from a single snapshot, no history — so
the whole set works today on the chains already on disk. History (percentiles) is a later phase.

The greeks CBOE ships (``delta``) drive ATM/25-delta selection directly, so no spot, forward, or
Black-Scholes inversion is needed for B1 (that arrives with the RND work in B2).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["SIGNAL_COLUMNS", "compute_signals", "realized_vol"]

# One row per (snapshot_date, underlying). Kept explicit so the CSV schema is stable and the
# DQ gate / BFF can rely on it regardless of which signals happened to be computable for a name.
SIGNAL_COLUMNS = [
    "snapshot_date", "underlying",
    "atm_iv_30d", "atm_iv_90d",
    "term_slope", "term_ratio", "term_inverted",
    "skew_25d_30d", "skew_25d_3m",
    "realized_vol_20d", "realized_vol_60d", "vrp_20d",
    "pc_oi_ratio", "pc_volume_ratio", "oi_hhi", "oi_top5_share",
    # B2 (signals 3 + 5): expected move + risk-neutral-density tail probabilities. Filled by the
    # runner via rnd.py; null on a chain too thin to fit a smile (a bad density is worse than none).
    "expected_move_front", "expected_move_front_dte",
    "rnd_dte", "rnd_forward", "rnd_spot",
    "prob_down_10", "prob_down_20", "prob_down_30", "prob_up_10", "prob_up_20",
    "n_contracts", "n_expiries",
]


def _atm_iv_for_expiry(g: pd.DataFrame) -> float | None:
    """ATM implied vol for one expiry: mean of the call nearest +0.5d and the put nearest -0.5d.

    Falls back to whichever side exists. Uses the chain's own delta, so it needs no spot/forward.
    """
    ivs: list[float] = []
    for pc, target in (("call", 0.5), ("put", -0.5)):
        side = g[g["put_call"] == pc]
        if side.empty:
            continue
        iv = _iv_at_delta(side, target)
        if iv is not None:
            ivs.append(iv)
    return float(np.mean(ivs)) if ivs else None


def _iv_at_delta(side: pd.DataFrame, target: float) -> float | None:
    """IV of the contract whose delta is closest to ``target`` within one expiry+side."""
    d = side.dropna(subset=["delta", "iv"])
    if d.empty:
        return None
    idx = (d["delta"] - target).abs().idxmin()
    # Guard: if the nearest delta is far from target (thin/one-sided chain) the point is unreliable.
    if abs(float(d.loc[idx, "delta"]) - target) > 0.20:
        return None
    return float(d.loc[idx, "iv"])


def _atm_term(per_expiry: pd.DataFrame, target_dte: float) -> float | None:
    """Linear-interpolate ATM IV vs DTE to ``target_dte`` (clamped to the observed DTE range)."""
    pts = per_expiry.dropna(subset=["atm_iv"]).sort_values("dte")
    if pts.empty:
        return None
    dte = pts["dte"].to_numpy(dtype=float)
    iv = pts["atm_iv"].to_numpy(dtype=float)
    if len(dte) == 1:
        return float(iv[0])
    t = float(np.clip(target_dte, dte.min(), dte.max()))
    return float(np.interp(t, dte, iv))


def _skew_25d(sub: pd.DataFrame, per_expiry: pd.DataFrame, target_dte: float) -> float | None:
    """``IV(25d put) - IV(25d call)`` at the expiry nearest ``target_dte``.

    Positive = puts richer than calls = the market paying up for downside (crash fear).
    """
    if per_expiry.empty:
        return None
    row = per_expiry.iloc[(per_expiry["dte"] - target_dte).abs().argmin()]
    g = sub[sub["days_to_expiry"] == row["dte"]]
    put25 = _iv_at_delta(g[g["put_call"] == "put"], -0.25)
    call25 = _iv_at_delta(g[g["put_call"] == "call"], 0.25)
    if put25 is None or call25 is None:
        return None
    return put25 - call25


def realized_vol(closes: pd.Series, window: int) -> float | None:
    """Annualized realized vol from a close series: std of daily log returns x √252.

    ``closes`` must already be filtered to on-or-before the snapshot date and sorted ascending.
    Returns ``None`` when there are too few points for the window.
    """
    c = pd.to_numeric(closes, errors="coerce").dropna()
    if len(c) < window + 1:
        return None
    rets = np.log(c / c.shift(1)).dropna().tail(window)
    if len(rets) < window:
        return None
    return float(rets.std(ddof=1) * np.sqrt(252.0))


def compute_signals(
    chain: pd.DataFrame,
    underlying: str,
    snapshot_date: str,
    closes: pd.Series | None = None,
    *,
    min_iv: float = 0.03,
    max_iv: float = 2.5,
    min_oi: int = 0,
    target_dte: int = 30,
    back_dte: int = 90,
    rv_short: int = 20,
    rv_long: int = 60,
) -> dict[str, object]:
    """Compute the B1 signal row for one (underlying, snapshot_date).

    ``chain`` is that underlying's rows for the single ``snapshot_date``. ``closes`` is the same
    name's ascending daily close history up to and including the snapshot date (for realized vol /
    VRP); pass ``None`` when no spot history exists for the name (e.g. bond-only issuers), and the
    realized-vol / VRP fields come back ``None`` rather than raising.

    Fields that cannot be computed from the available chain are ``None`` — a thin or one-sided
    chain degrades gracefully to fewer signals, it does not fail the row.
    """
    row: dict[str, object] = dict.fromkeys(SIGNAL_COLUMNS)
    row["snapshot_date"] = snapshot_date
    row["underlying"] = underlying

    clean = chain[(chain["iv"] > min_iv) & (chain["iv"] < max_iv)
                  & (chain["mid"] > 0) & (chain["days_to_expiry"] >= 1)]
    if min_oi > 0 and "open_interest" in clean.columns:
        clean = clean[clean["open_interest"].fillna(0) >= min_oi]
    row["n_contracts"] = len(clean)
    if clean.empty:
        return row

    # -- per-expiry ATM IV -------------------------------------------------- #
    per_rows = []
    for dte, g in clean.groupby("days_to_expiry"):
        per_rows.append({"dte": int(dte), "atm_iv": _atm_iv_for_expiry(g)})
    per_expiry = pd.DataFrame(per_rows)
    row["n_expiries"] = len(per_expiry)

    # 1. ATM IV level, interpolated to the target (front) tenor.
    usable = per_expiry.dropna(subset=["atm_iv"])
    atm_30 = _atm_term(per_expiry, target_dte)
    row["atm_iv_30d"] = atm_30

    # 2. Term structure — 30d (front) vs 90d (back), the VIX-vs-VIX3M convention. Interpolated to
    #    both tenors so a single noisy weekly expiry (near-dated IV humps are common) can't drive
    #    the inversion flag; that is exactly the 7d-hump artefact an "earliest expiry" front hits.
    #    Needs >=2 distinct expiries: with one, 30d and 90d collapse to the same point and the
    #    slope would be a spurious zero, so term fields stay null (genuinely unknown).
    if len(usable) >= 2:
        atm_90 = _atm_term(per_expiry, back_dte)
        row["atm_iv_90d"] = atm_90
        if atm_30 is not None and atm_90 is not None:
            row["term_slope"] = atm_90 - atm_30                 # >0 = normal upward term structure
            row["term_ratio"] = atm_30 / atm_90 if atm_90 else None
            row["term_inverted"] = bool(atm_30 > atm_90)       # front richer than back = near-term stress

    # 4. 25-delta skew at 30d and 3m.
    row["skew_25d_30d"] = _skew_25d(clean, usable, target_dte)
    row["skew_25d_3m"] = _skew_25d(clean, usable, back_dte)

    # 7. Variance risk premium (ATM IV - realized vol), plus the raw realized vols.
    if closes is not None:
        rv20 = realized_vol(closes, rv_short)
        rv60 = realized_vol(closes, rv_long)
        row["realized_vol_20d"] = rv20
        row["realized_vol_60d"] = rv60
        if atm_30 is not None and rv20 is not None:
            row["vrp_20d"] = atm_30 - rv20                      # >0 = implied over realized (cushion)

    # 8. Positioning: put/call OI + volume ratios and OI concentration across strikes.
    _positioning(clean, row)
    return row


def _positioning(clean: pd.DataFrame, row: dict[str, object]) -> None:
    if "open_interest" in clean.columns:
        oi = clean.groupby("put_call")["open_interest"].sum(min_count=1)
        call_oi, put_oi = float(oi.get("call", 0.0) or 0.0), float(oi.get("put", 0.0) or 0.0)
        row["pc_oi_ratio"] = put_oi / call_oi if call_oi > 0 else None
        by_strike = clean.groupby("strike")["open_interest"].sum(min_count=1).dropna()
        total = float(by_strike.sum())
        if total > 0:
            shares = (by_strike / total).to_numpy()
            row["oi_hhi"] = float(np.sum(shares ** 2))          # Herfindahl: 1/n_strikes .. 1
            top5 = float(np.sort(shares)[-5:].sum())
            row["oi_top5_share"] = top5
    if "volume" in clean.columns:
        vol = clean.groupby("put_call")["volume"].sum(min_count=1)
        call_v, put_v = float(vol.get("call", 0.0) or 0.0), float(vol.get("put", 0.0) or 0.0)
        row["pc_volume_ratio"] = put_v / call_v if call_v > 0 else None
