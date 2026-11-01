"""Equity (listed-option) market-data adapter.

Turns an option chain CSV in `data/marketdata/options/chains/` into an
`EquityMarketData` (spot, dividend yield, vol surface). The option underliers'
spot and dividends are NOT in the price/dividend files, so both are recovered
**from the chain itself** via put-call parity:

  * For each expiry, the implied forward F(T) = K + e^{rT}(C-P) at the ATM strike.
  * Regress ln F(T) on T across expiries -> intercept = ln(spot), slope = r - q,
    giving the spot and the chain-implied continuous dividend yield. (Validated to
    ~0.07% on NVDA, whose spot is independently known.)

The vol surface is the per-(expiry, strike) implied vol from the chain.
Pure pandas/numpy — no QuantLib here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import storage

from ..input.common.market_data import EquityMarketData, EquityVolSurface
from ..input.common.valuation import ValuationContext
from .snapshot import MarketDataSnapshot, SnapshotReader
from .staleness import pick_snapshot


class EquityMarketDataAdapter(SnapshotReader):
    def __init__(self, config: dict, project_root: Path,
                 snapshot: MarketDataSnapshot | None = None):
        self.cfg = config.get("options", {})
        self.root = project_root
        md = storage.join(storage.data_base(project_root), "marketdata")
        self.chains_dir = storage.join(md, "options", "chains")
        self.dividends_path = storage.join(
            md, "equity", self.cfg.get("dividends_file", "dividends.csv"))
        self._snapshot = snapshot

    def equity_market(self, underlier: str, ctx: ValuationContext) -> tuple[EquityMarketData, date, list[str]]:
        path = storage.join(self.chains_dir, f"{underlier}_chains.csv")
        if not storage.exists(path):
            raise FileNotFoundError(f"Option chain not found for {underlier}: {path}")

        df = self._read_csv(path, parse_dates=["snapshot_date", "expiry_date"])
        chosen, warnings = pick_snapshot(df["snapshot_date"], ctx, label=f"{underlier} option chain",
                                         kind="equity_chain")
        sub = df[df["snapshot_date"] == chosen].copy()
        as_of = chosen.date()

        spot, q = self._recover_spot_and_div(sub)
        surface = self._build_surface(sub, underlier, as_of)
        dividends = self._dividend_schedule(underlier, as_of)

        return (
            EquityMarketData(
                underlier=underlier, currency="USD", spot=spot, dividend_yield=q,
                as_of_date=as_of, vol_surface=surface, dividends=dividends,
            ),
            as_of, warnings,
        )

    # ------------------------------------------------------------------ #
    def _dividend_schedule(self, underlier: str, as_of: date) -> tuple[tuple[date, float], ...]:
        """Projected discrete ex-dividend schedule (ex_date, per-share amount) for the
        underlier, covering `as_of` out to the projection horizon.

        The free dividends feed is historical, so future ex-dates are projected by rolling
        the most recent ex-date forward at the observed cadence (median gap of the last few
        payments) with the latest per-share amount held constant. Returns () for non-payers
        (ticker absent) or when the feed is unavailable/too short.
        """
        if not storage.exists(self.dividends_path):
            return ()
        horizon_years = float(self.cfg.get("dividend_projection_horizon_years", 2.0))
        df = self._read_csv(self.dividends_path, parse_dates=["date"])
        if not {"date", "ticker", "amount"} <= set(df.columns):
            return ()
        hist = (df[(df["ticker"].astype(str).str.upper() == underlier.upper())
                   & (df["date"] <= pd.Timestamp(as_of))]
                .dropna(subset=["date", "amount"]).sort_values("date"))
        if len(hist) < 4:
            return ()

        dates = hist["date"].tolist()
        last_amt = float(hist["amount"].iloc[-1])
        gaps = [(dates[i] - dates[i - 1]).days for i in range(len(dates) - 4, len(dates))]
        cadence = int(np.median([g for g in gaps if g > 0])) if gaps else 91
        cadence = min(max(cadence, 25), 200)          # guard against outliers (~monthly..semiannual)

        horizon = pd.Timestamp(as_of) + pd.Timedelta(days=int(horizon_years * 365))
        out: list[tuple[date, float]] = []
        nxt = dates[-1] + pd.Timedelta(days=cadence)
        while nxt <= horizon:
            out.append((nxt.date(), last_amt))
            nxt = nxt + pd.Timedelta(days=cadence)
        return tuple(out)

    # ------------------------------------------------------------------ #
    def _recover_spot_and_div(self, sub: pd.DataFrame) -> tuple[float, float]:
        r = float(self.cfg.get("forward_recovery_rate", 0.04))
        min_pairs = int(self.cfg.get("min_strike_pairs", 5))
        min_t = float(self.cfg.get("recovery_min_t", 0.02))
        max_t = float(self.cfg.get("recovery_max_t", 1.5))

        rows = []
        for _exp, g in sub.groupby("expiry_date"):
            t = g["days_to_expiry"].iloc[0] / 365.0
            if not (min_t <= t <= max_t):
                continue
            calls = g[(g["put_call"] == "call") & (g["mid"] > 0)].groupby("strike")["mid"].median()
            puts = g[(g["put_call"] == "put") & (g["mid"] > 0)].groupby("strike")["mid"].median()
            common = sorted(set(calls.index) & set(puts.index))
            if len(common) < min_pairs:
                continue
            fwds = pd.DataFrame({"K": common,
                                 "F": [K + np.exp(r * t) * (calls[K] - puts[K]) for K in common]})
            fwds["dist"] = (fwds["K"] - fwds["F"]).abs()
            f_atm = fwds.sort_values("dist").head(5)["F"].median()
            if f_atm > 0:
                rows.append((t, f_atm))

        if len(rows) < 2:
            raise ValueError("Insufficient call/put pairs to recover spot via put-call parity")

        arr = pd.DataFrame(rows, columns=["T", "F"]).sort_values("T")
        slope, intercept = np.polyfit(arr["T"], np.log(arr["F"]), 1)
        spot = float(np.exp(intercept))
        div_yield = float(r - slope)
        return spot, div_yield

    # ------------------------------------------------------------------ #
    def _build_surface(self, sub: pd.DataFrame, underlier: str, as_of: date) -> EquityVolSurface:
        min_iv = float(self.cfg.get("min_iv", 0.03))
        max_iv = float(self.cfg.get("max_iv", 2.5))
        clean = sub[(sub["iv"] > min_iv) & (sub["iv"] < max_iv)
                    & (sub["days_to_expiry"] >= 1) & (sub["mid"] > 0)]
        # one IV per (expiry, strike): median across call/put
        grid = clean.groupby(["days_to_expiry", "strike"])["iv"].median().reset_index()
        if grid.empty:
            raise ValueError(f"No usable IV points to build a vol surface for {underlier}")
        return EquityVolSurface(
            underlier=underlier,
            days_to_expiry=tuple(int(d) for d in grid["days_to_expiry"]),
            strikes=tuple(float(k) for k in grid["strike"]),
            ivs=tuple(float(v) for v in grid["iv"]),
            as_of_date=as_of,
        )
