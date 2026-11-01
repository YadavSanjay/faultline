"""Curve sensitivities of the curve-dependent trades, for the institutional gap review.

Feeds docs/design/institutional_gap_review.md (rates, bonds, FX sections). Prices the rates, bonds,
FX and credit trades with the production engine, then bumps curve inputs one at a time:

* curve fidelity: the bootstrapped 14-node SOFR curve vs the Pensford monthly forwards it comes from,
  and the PV change from rebuilding on the raw monthly discount factors instead;
* discount-only / forecast-only / both +1bp (as booked, and with option strikes frozen at today's ATM,
  since #23-25 are booked with no strike and re-struck daily);
* key-rate DV01 per SOFR par node (fixed strikes); Treasury-curve DV01 for the Treasuries;
* FX: cross-currency basis shifts and USD discount-only +1bp;
* long-end scenarios on the 20Y/30Y nodes.

Writes ``curve_sensitivities.json`` to the output folder (default
``data/analysis/gap_review/<valuation-date>/``). Run from the repo root:

    conda run --no-capture-output -n py312 python scripts/analysis/gap_review/curve_sensitivities.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import QuantLib as ql

from src.common import storage
from src.pricer.config import load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.market_data import FxData, MarketDataSet, ParOISCurveData, TreasuryCurveData
from src.pricer.loader.rates_adapter import RatesMarketDataAdapter
from src.pricer.portfolio.portfolio_loader import load_portfolio, load_portfolio_config
from src.pricer.ql.common import curve_builder as cb
from src.pricer.ql.common.curve_cache import curve_cache
from src.pricer.ql.factory import get_pricer
from src.pricer.risk import bumps
from src.pricer.runner import _build_ctx

BP = 1e-4
CURVE_ASSET_CLASSES = ("rates", "bonds", "fx", "credit")
LONG_END_SCENARIOS = {"30Y-15bp": {"30Y": -15 * BP}, "30Y-25bp": {"30Y": -25 * BP},
                      "20Y&30Y-10bp": {"20Y": -10 * BP, "30Y": -10 * BP}}

Transform = Callable[[str, object], object]


def _bump_items(mds: MarketDataSet, fn: Transform) -> MarketDataSet:
    """A copy of `mds` with every item passed through `fn(key, item)`."""
    return MarketDataSet(valuation_date=mds.valuation_date, items={k: fn(k, v) for k, v in mds.items.items()},
                         warnings=list(mds.warnings), as_of_dates=dict(mds.as_of_dates), snapshot_id=mds.snapshot_id)


def _ois_shift(purpose: str | None, d: float | dict[str, float], tenor: str | None = None) -> Transform:
    """Shift only the SOFR par curve (not the Treasury curve) for 'discount', 'forecast' or both (None).

    `d` is a parallel shift (decimal), or a {tenor: shift} map; `tenor` restricts a parallel shift to one node.
    """
    def fn(key: str, item: object) -> object:
        if isinstance(item, ParOISCurveData) and (purpose is None or f"|{purpose}|" in key):
            if isinstance(d, dict):
                rates = tuple(r + d.get(t, 0.0) for r, t in zip(item.par_rates, item.tenors, strict=True))
            else:
                rates = tuple(r + (d if tenor in (None, t) else 0.0)
                              for r, t in zip(item.par_rates, item.tenors, strict=True))
            return replace(item, par_rates=rates)
        return item
    return fn


def _fx_basis(bp: float) -> Transform:
    def fn(_key: str, item: object) -> object:
        return replace(item, xccy_basis_bp=item.xccy_basis_bp + bp) if isinstance(item, FxData) else item
    return fn


def analyse(vdate: date, book: str | None) -> dict[str, Any]:
    """Runs every measurement for the curve-dependent trades at `vdate` and returns the results."""
    cfg = load_pricer_config()
    eng = PricingEngine(cfg, _ROOT)
    ctx = _build_ctx(cfg, vdate, "PV")
    trades = [t for t in load_portfolio(book=book) if t.asset_class.value in CURVE_ASSET_CLASSES]
    out: dict[str, Any] = {"valuation_date": vdate.isoformat(), "book": book or "default",
                           "snapshot_id": eng.snapshot_id}

    def pv(trade: Any, mds: MarketDataSet) -> float:
        return float(get_pricer(trade).price(trade, mds, ctx).pv)

    # Base PVs and measures.
    base: dict[str, Any] = {}
    mdss: dict[str, MarketDataSet] = {}
    with curve_cache():
        for t in trades:
            mds = eng.loader.resolve(t.market_data_requirements(ctx), ctx)
            mdss[t.trade_id] = mds
            r = get_pricer(t).price(t, mds, ctx)
            base[t.trade_id] = {"type": t.trade_type.value, "pv": r.pv,
                                "measures": {k: v for k, v in r.measures.items() if isinstance(v, int | float)}}
    out["base"] = base

    def fixed_strike(t: Any) -> Any:
        """The trade with its strike frozen at today's ATM (#23-25 are booked strike=None)."""
        if hasattr(t, "strike") and t.strike is None:
            return replace(t, strike=base[t.trade_id]["measures"].get("atm_or_strike"))
        return t

    # Curve fidelity: bootstrapped curve vs the Pensford monthly forwards.
    ra = RatesMarketDataAdapter(cfg, _ROOT, snapshot=eng.snapshot)
    curve_data, as_of, _ = ra.par_ois_curve(ctx)
    out["par_nodes_pct"] = dict(zip(curve_data.tenors, [round(r * 100, 4) for r in curve_data.par_rates],
                                    strict=True))
    raw = storage.read_csv(storage.data_path("marketdata", "rates", cfg["rates"].get("pensford_file",
                                                                                     "pensford_sofr_forward.csv")),
                           parse_dates=["as_of_date", "settlement_date"])
    sub = (raw[raw.as_of_date == pd.Timestamp(as_of)].sort_values("settlement_date")
           .drop_duplicates("settlement_date"))
    offs, dfs = ra._discount_factors(sub, as_of)
    ql.Settings.instance().evaluationDate = ql.Date(vdate.day, vdate.month, vdate.year)
    curve = cb._build_ois_curve(curve_data, "logcubic").currentLink()
    ref = curve.referenceDate()
    settle, fwd = sub.settlement_date.dt.date.tolist(), sub.forward_sofr.tolist()
    rows = []
    for i in range(len(settle) - 1):
        s, e = settle[i], settle[i + 1]
        qs, qe = ql.Date(s.day, s.month, s.year), ql.Date(e.day, e.month, e.year)
        if qs >= ref:
            f = curve.forwardRate(qs, qe, ql.Actual360(), ql.Simple).rate()
            rows.append({"yrs": (s - vdate).days / 365.25, "err_bp": (f - fwd[i]) / BP})
    fe = pd.DataFrame(rows)
    out["forward_fidelity_bp"] = {
        f"{a}-{b}Y": {"rms": round(float(np.sqrt((g.err_bp ** 2).mean())), 2),
                      "max_abs": round(float(g.err_bp.abs().max()), 2)}
        for a, b in ((0, 1), (1, 2), (2, 5), (5, 10), (10, 20))
        for g in [fe[(fe.yrs >= a) & (fe.yrs < b)]] if len(g)}

    # Rebuild on the raw monthly discount factors (no compression to 14 nodes), same extrapolation.
    dates = [ref] + [ref + int(o) for o in offs[1:]]
    values = [1.0] + [float(x) for x in dfs[1:]]
    ln = np.log(dfs)
    slope = (ln[-1] - ln[-2]) / (offs[-1] - offs[-2])
    for yrs in (25, 30, 40, 50):
        dd = yrs * 365.25
        dates.append(ref + int(dd))
        values.append(float(np.exp(ln[-1] + slope * (dd - offs[-1]))))

    def monthly_build(_curve_data: Any, _interpolation: str) -> ql.YieldTermStructureHandle:
        c = ql.DiscountCurve(dates, values, ql.Actual365Fixed())    # log-linear DF = piecewise-flat forward
        c.enableExtrapolation()
        return ql.YieldTermStructureHandle(c)

    original = cb._build_ois_curve
    cb._build_ois_curve = monthly_build                              # this script only; restored below
    try:
        with curve_cache():
            out["monthly_curve_pv_change"] = {t.trade_id: pv(t, mdss[t.trade_id]) - base[t.trade_id]["pv"]
                                              for t in trades}
    finally:
        cb._build_ois_curve = original

    # Discount-only / forecast-only / both (+1bp, central difference), booked and with fixed strikes.
    split: dict[str, dict[str, float]] = {}
    with curve_cache():
        for t in trades:
            row: dict[str, float] = {}
            for label, tt in (("booked", t), ("fixed_strike", fixed_strike(t))):
                if label == "fixed_strike" and tt is t:
                    continue
                m = mdss[t.trade_id]
                for purpose in ("discount", "forecast", None):
                    up = pv(tt, _bump_items(m, _ois_shift(purpose, BP)))
                    dn = pv(tt, _bump_items(m, _ois_shift(purpose, -BP)))
                    row[f"{label}:{purpose or 'both'}"] = (up - dn) / 2
            split[t.trade_id] = row
    out["dv01_split"] = split

    # Key-rate DV01 per SOFR node (fixed strikes) and Treasury-curve DV01.
    kr: dict[str, dict[str, float]] = {}
    ust: dict[str, float] = {}
    with curve_cache():
        for t in trades:
            m = mdss[t.trade_id]
            if any(isinstance(v, ParOISCurveData) for v in m.items.values()):
                tt = fixed_strike(t)
                kr[t.trade_id] = {
                    n: (pv(tt, _bump_items(m, _ois_shift(None, BP, n)))
                        - pv(tt, _bump_items(m, _ois_shift(None, -BP, n)))) / 2
                    for n in curve_data.tenors}
            if any(isinstance(v, TreasuryCurveData) for v in m.items.values()):
                ust[t.trade_id] = (pv(t, bumps.bumped(m, bumps.shift_ir(BP)))
                                   - pv(t, bumps.bumped(m, bumps.shift_ir(-BP)))) / 2
    out["key_rate_dv01"] = kr
    out["treasury_dv01"] = ust

    # FX: cross-currency basis and USD discount-only.
    fx: dict[str, dict[str, float]] = {}
    with curve_cache():
        for t in trades:
            m = mdss[t.trade_id]
            fxd = next((v for v in m.items.values() if isinstance(v, FxData)), None)
            if fxd is None:
                continue
            p0 = base[t.trade_id]["pv"]
            row = {"spot": fxd.spot, "foreign_rate": fxd.foreign_rate}
            for bp in (-100, -50, -25, -10, 10, 25):
                row[f"basis{bp:+d}bp"] = pv(t, _bump_items(m, _fx_basis(bp))) - p0
            row["usd_discount_+1bp"] = pv(t, _bump_items(m, _ois_shift("discount", BP))) - p0
            fx[t.trade_id] = row
    out["fx"] = fx

    # Long-end scenarios.
    long_end: dict[str, dict[str, float]] = {}
    with curve_cache():
        for t in trades:
            m = mdss[t.trade_id]
            if any(isinstance(v, ParOISCurveData) for v in m.items.values()):
                tt = fixed_strike(t)
                p0 = pv(tt, m)
                long_end[t.trade_id] = {n: pv(tt, _bump_items(m, _ois_shift(None, d))) - p0
                                        for n, d in LONG_END_SCENARIOS.items()}
    out["long_end"] = long_end
    return out


def main() -> None:
    """Parses arguments, runs the analysis and writes the JSON."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--valuation-date", default=None, help="ISO date; default: the book's default")
    p.add_argument("--book", default=None, help="book variant (default: active default book)")
    p.add_argument("--out", default=None, help="output folder (default: data/analysis/gap_review/<date>)")
    a = p.parse_args()
    vdate = (date.fromisoformat(a.valuation_date) if a.valuation_date
             else load_portfolio_config(book=a.book).default_valuation_date)
    out_dir = a.out or storage.data_path("analysis", "gap_review", vdate.isoformat())
    result = analyse(vdate, a.book)
    path = storage.join(str(out_dir), "curve_sensitivities.json")
    storage.makedirs_for(path)
    storage.write_text_atomic(path, json.dumps(result, indent=1, default=str))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
