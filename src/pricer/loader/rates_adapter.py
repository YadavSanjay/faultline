"""Rates market-data adapter.

Turns the raw CSVs in `data/marketdata/rates/` into resolved domain inputs:

* `par_ois_curve` — derive par SOFR-OIS swap rates at standard tenors from the
  Pensford published forward curve (the binding curve input), via discount factors
  compounded off the monthly forward-SOFR grid (Act/360).
* `normal_vol` — flat normal (Bachelier) vol level from the pricer config, anchored
  to the latest MOVE level when available.

Returns `(domain_object, as_of_date, warnings)` so the loader can attach staleness
diagnostics. Pure pandas/numpy — no QuantLib here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import storage

from ..input.common.conventions import IndexConvention
from ..input.common.market_data import FlatNormalVolData, ParOISCurveData
from ..input.common.valuation import ValuationContext
from .snapshot import MarketDataSnapshot, SnapshotReader
from .staleness import pick_snapshot

_DEFAULT_TENORS = ("1M", "3M", "6M", "9M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y",
                   "12Y", "15Y", "20Y", "30Y")


class RatesMarketDataAdapter(SnapshotReader):
    def __init__(self, config: dict, project_root: Path,
                 snapshot: MarketDataSnapshot | None = None):
        self.cfg = config.get("rates", {})
        self.root = project_root
        self.data_dir = storage.join(storage.data_base(project_root), "marketdata", "rates")
        self._snapshot = snapshot

    # ------------------------------------------------------------------ #
    # SOFR OIS par curve
    # ------------------------------------------------------------------ #
    def par_ois_curve(self, ctx: ValuationContext) -> tuple[ParOISCurveData, date, list[str]]:
        warnings: list[str] = []
        path = storage.join(self.data_dir, self.cfg.get("pensford_file", "pensford_sofr_forward.csv"))
        if not storage.exists(path):
            raise FileNotFoundError(f"Pensford forward curve not found: {path}")

        df = self._read_csv(path, parse_dates=["as_of_date", "settlement_date"])
        as_of_ts, warn = pick_snapshot(df["as_of_date"], ctx, label="SOFR forward-curve",
                                       kind="ois_curve")
        warnings += warn

        sub = (df[df["as_of_date"] == as_of_ts]
               .sort_values("settlement_date")
               .drop_duplicates("settlement_date"))
        as_of = as_of_ts.date()

        # discount factors off the monthly forward grid (Act/360 compounding)
        day_offsets, dfs = self._discount_factors(sub, as_of)

        tenor_strs = tuple(self.cfg.get("par_tenors", _DEFAULT_TENORS))
        par_rates = tuple(
            self._par_ois_rate(day_offsets, dfs, as_of, t) for t in tenor_strs
        )

        curve = ParOISCurveData(
            currency="USD",
            index=IndexConvention.usd_sofr(),
            as_of_date=as_of,
            tenors=tenor_strs,
            par_rates=par_rates,
            source=f"pensford_forward_curve@{as_of.isoformat()}",
        )
        return curve, as_of, warnings

    @staticmethod
    def _discount_factors(sub: pd.DataFrame, as_of: date) -> tuple[np.ndarray, np.ndarray]:
        """Compound the monthly forward grid into discount factors.

        forward_sofr[i] is the projected SOFR for the period starting at
        settlement_date[i]; DF advances DF_{i+1} = DF_i / (1 + f_i * tau_i),
        tau Act/360. Returns (day_offsets_from_as_of, discount_factors), both
        including the anchor point (0, 1.0).
        """
        settle = sub["settlement_date"].dt.date.to_numpy()
        fwd = sub["forward_sofr"].to_numpy(dtype=float)

        offsets = [0.0]
        dfs = [1.0]
        df_cur = 1.0
        for i in range(len(settle) - 1):
            tau = (settle[i + 1] - settle[i]).days / 360.0
            df_cur = df_cur / (1.0 + fwd[i] * tau)
            offsets.append((settle[i + 1] - as_of).days)
            dfs.append(df_cur)
        return np.asarray(offsets), np.asarray(dfs)

    @staticmethod
    def _df_at(day_offsets: np.ndarray, dfs: np.ndarray, target_days: float) -> float:
        """Discount factor at `target_days`, log-linear in DF.

        Within the grid this is ordinary log-linear interpolation. **Beyond the last
        grid node** (the Pensford path reaches ~20Y) we extrapolate the log-DF linearly
        along the slope of the final monthly segment — i.e. hold the last instantaneous
        forward flat (constant-forward extrapolation), rather than `np.interp`'s flat
        clamp which would freeze the DF and imply a ~0% forward past 20Y. This is what
        makes a synthesised long-end (25Y/30Y) par node meaningful for the ORCL 2053 /
        META 2054 bonds and the 5Yx10Y swaption underlying (A1).
        """
        ln = np.log(dfs)
        # A single grid point (degenerate/collapsed path) has no segment slope to
        # extend — fall back to the flat clamp rather than indexing ln[-2].
        if len(day_offsets) < 2 or target_days <= day_offsets[-1]:
            return float(np.exp(np.interp(target_days, day_offsets, ln)))
        slope = (ln[-1] - ln[-2]) / (day_offsets[-1] - day_offsets[-2])
        return float(np.exp(ln[-1] + slope * (target_days - day_offsets[-1])))

    def _par_ois_rate(self, day_offsets: np.ndarray, dfs: np.ndarray, as_of: date, tenor: str) -> float:
        """Par OIS rate (Act/360) for a tenor string like '3M', '1Y', '10Y'.

        OIS convention: a single fixed payment at maturity for sub-1Y tenors,
        annual fixed coupons for >=1Y tenors. par = (1 - DF(mat)) / annuity.
        """
        n, unit = int(tenor[:-1]), tenor[-1].upper()
        if unit == "M":
            coupon_dates = [as_of, (pd.Timestamp(as_of) + pd.DateOffset(months=n)).date()]
        elif unit == "Y":
            coupon_dates = [
                (pd.Timestamp(as_of) + pd.DateOffset(years=k)).date()
                for k in range(n + 1)
            ]
        else:
            raise ValueError(f"Unsupported tenor unit in {tenor!r} (expected 'M' or 'Y')")

        annuity = 0.0
        for k in range(1, len(coupon_dates)):
            tau = (coupon_dates[k] - coupon_dates[k - 1]).days / 360.0
            d_k = (coupon_dates[k] - as_of).days
            annuity += tau * self._df_at(day_offsets, dfs, d_k)
        df_mat = self._df_at(day_offsets, dfs, (coupon_dates[-1] - as_of).days)
        return (1.0 - df_mat) / annuity

    # ------------------------------------------------------------------ #
    # Flat normal vol (level anchored to the MOVE index, point-in-time)
    # ------------------------------------------------------------------ #
    def normal_vol(self, tenor: str | None, ctx: ValuationContext) -> tuple[FlatNormalVolData, date]:
        """Flat Bachelier normal-vol level for swaptions/cap (#23-25).

        The LEVEL is derived live from the ICE BofA MOVE index (annualized bp of
        Treasury-yield implied vol ≈ a normal-vol level): ``vol = MOVE_bp/1e4 x
        move_scale``, using the latest MOVE observation on/before the valuation
        date (point-in-time), clamped to a sane band. Falls back to the configured
        ``flat_level`` when MOVE is unavailable. Flat across strike & expiry — the
        smile/cube needs licensed data (the free DTCC SDR IR-options feed is not
        calibratable; see the config note).
        """
        vcfg = self.cfg.get("normal_vol", {})
        fallback = float(vcfg.get("flat_level", 0.0090))     # 90bp default fallback
        scale = float(vcfg.get("move_scale", 1.0))
        lo = float(vcfg.get("min_bp", 10)) / 1e4
        hi = float(vcfg.get("max_bp", 500)) / 1e4
        level = fallback
        as_of = ctx.valuation_date
        anchor = f"config fallback {fallback * 1e4:.0f}bp (MOVE unavailable)"

        move_path = storage.join(storage.data_base(self.root), "marketdata", "stress",
                                 vcfg.get("move_file", "move_index.csv"))
        if storage.exists(move_path):
            try:
                move = self._read_csv(move_path)
                date_col = next((c for c in move.columns if c.lower() in ("date", "time")), move.columns[0])
                move[date_col] = pd.to_datetime(move[date_col], errors="coerce")
                val_col = vcfg.get("move_col") or next(
                    (c for c in move.columns if "close" in c.lower() or "move" in c.lower()),
                    move.columns[-1])
                hist = (move.dropna(subset=[date_col, val_col])
                        .loc[move[date_col] <= pd.Timestamp(ctx.valuation_date)]
                        .sort_values(date_col))
                if not hist.empty:
                    row = hist.iloc[-1]
                    move_bp = float(row[val_col])
                    level = min(max(move_bp * scale / 1e4, lo), hi)
                    as_of = row[date_col].date()
                    anchor = (f"MOVE-derived: {move_bp:.1f}bp x{scale:g} = {level * 1e4:.1f}bp "
                              f"(MOVE {as_of.isoformat()})")
            except Exception:  # never fail pricing on the vol anchor — keep the fallback
                pass

        # C1: shape the flat MOVE level into an expiry-dependent normal-vol term structure via a
        # config multiplier curve (a stylized rates normal-vol shape, MOVE-anchored — a placeholder
        # for a real swaption vol surface). This makes the Bermudan co-terminal calibration
        # non-degenerate (the slope identifies mean reversion) and gives each option its expiry's
        # vol. Still flat across strike. Empty shape => flat (backward compatible).
        term_structure = self._normal_vol_term_structure(vcfg, level, lo, hi)
        vol = FlatNormalVolData(currency="USD", vol=level, as_of_date=as_of,
                                anchor=anchor, tenor=tenor, term_structure=term_structure)
        return vol, as_of

    @staticmethod
    def _normal_vol_term_structure(vcfg: dict, level: float, lo: float, hi: float
                                   ) -> tuple[tuple[float, float], ...]:
        """Expiry-dependent normal vol = MOVE `level` x a config multiplier per expiry tenor
        (`normal_vol.term_structure`, e.g. {"1M":1.0,"5Y":0.95}). Returns ascending
        (expiry_years, vol) pairs, clamped to the sane band; () when no shape is configured."""
        shape = vcfg.get("term_structure", {})
        if not shape:
            return ()
        pts = []
        for tstr, mult in shape.items():
            n, unit = float(tstr[:-1]), tstr[-1].upper()
            years = n / 12.0 if unit == "M" else n
            pts.append((years, min(max(level * float(mult), lo), hi)))
        return tuple(sorted(pts))
