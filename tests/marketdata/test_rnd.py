"""Tests for the risk-neutral density / tail-probability derivation (B2, signal 5).

The key test builds a Black-Scholes-consistent synthetic chain at a FLAT vol, so the recovered
risk-neutral density must match the known lognormal — the ground-truth check for the
Breeden-Litzenberger numerics (design §3.8's "the one item with real numerical risk").
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from src.marketdata.options.rnd import _bs_forward_call as bs_call
from src.marketdata.options.rnd import (
    atm_expected_move,
    recover_forward_curve,
    risk_neutral_density,
)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_chain(spot: float, r: float, q: float, sigma: float,
              dtes=(30, 90), snapshot="2026-07-10") -> pd.DataFrame:
    """A Black-Scholes-consistent chain at a flat vol: mids are DISCOUNTED forward prices, so
    put-call parity (C - P = D*(F - K)) holds exactly and the RND is a known lognormal."""
    rows = []
    for dte in dtes:
        t = dte / 365.0
        disc = math.exp(-r * t)
        f = spot * math.exp((r - q) * t)
        strikes = np.round(np.linspace(0.6 * f, 1.4 * f, 41), 2)
        cfwd = bs_call(f, strikes, t, np.full_like(strikes, sigma))
        pfwd = cfwd - (f - strikes)                         # parity in the forward measure
        for k, c, p in zip(strikes, cfwd, pfwd, strict=True):
            d1 = (math.log(f / k) + 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))
            rows.append((snapshot, "call", float(k), dte, disc * float(c), sigma, _norm_cdf(d1)))
            rows.append((snapshot, "put", float(k), dte, disc * float(p), sigma, _norm_cdf(d1) - 1.0))
    return pd.DataFrame(rows, columns=["snapshot_date", "put_call", "strike",
                                       "days_to_expiry", "mid", "iv", "delta"])


def test_forward_and_spot_recovery():
    spot, r, q = 100.0, 0.04, 0.01
    chain = _bs_chain(spot, r, q, 0.30)
    rec_spot, fwds = recover_forward_curve(chain)
    assert rec_spot == pytest.approx(spot, rel=1e-3)
    for dte, (f, _t) in fwds.items():
        assert f == pytest.approx(spot * math.exp((r - q) * dte / 365.0), rel=1e-3)


def test_rnd_matches_lognormal_for_a_flat_smile():
    spot, r, q, sigma = 100.0, 0.03, 0.0, 0.30
    chain = _bs_chain(spot, r, q, sigma)
    res = risk_neutral_density(chain, "TST", "2026-07-10", target_dte=90)
    assert res is not None
    # density integrates to ~1 and the CDF is a valid monotone 0..1
    assert float(np.trapezoid(res.pdf, res.strikes)) == pytest.approx(1.0, abs=0.02)
    assert res.cdf.min() >= 0.0 and res.cdf.max() <= 1.0
    assert bool((np.diff(res.cdf) >= -1e-9).all())
    # analytic lognormal: P(S_T <= K) = N(-d2). Check at spot-anchored moves.
    t = res.dte / 365.0
    f = res.forward
    for mv in (-0.20, -0.10, 0.0):
        k = spot * (1 + mv)
        d2 = (math.log(f / k) - 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))
        got = float(np.interp(k, res.strikes, res.cdf))
        assert got == pytest.approx(_norm_cdf(-d2), abs=0.03)


def test_tail_probabilities_are_monotone():
    chain = _bs_chain(100.0, 0.03, 0.0, 0.35)
    res = risk_neutral_density(chain, "TST", "2026-07-10", target_dte=90)
    assert res is not None
    t = res.tail
    assert t["prob_down_30"] <= t["prob_down_20"] <= t["prob_down_10"]
    assert t["prob_up_20"] <= t["prob_up_10"]
    assert all(0.0 <= v <= 1.0 for v in t.values())


def test_expected_move_is_straddle_over_forward():
    chain = _bs_chain(100.0, 0.03, 0.0, 0.30)
    _spot, fwds = recover_forward_curve(chain)
    em = atm_expected_move(chain, fwds, target_dte=0)     # front expiry
    assert em is not None
    move, dte = em
    assert dte == 30                                       # nearest to 0 -> shortest
    # 30d ATM straddle at 30% vol ~ 0.8*sigma*sqrt(T) of forward, order-of-magnitude sanity
    assert 0.02 < move < 0.12


def test_thin_chain_returns_none_not_garbage():
    # one strike, one expiry -> cannot fit a smile
    df = pd.DataFrame({
        "snapshot_date": ["2026-07-10"] * 2,
        "put_call": ["call", "put"],
        "strike": [100.0, 100.0],
        "days_to_expiry": [90, 90],
        "mid": [5.0, 5.0],
        "iv": [0.3, 0.3],
        "delta": [0.5, -0.5],
    })
    assert risk_neutral_density(df, "X", "2026-07-10", 90) is None
