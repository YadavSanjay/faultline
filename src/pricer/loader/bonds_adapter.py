"""Bonds market-data adapter.

Turns the raw CSVs in `data/marketdata/bonds/` into resolved domain inputs:

* `treasury_curve` — UST par yields (Treasury.gov) -> TreasuryCurveData, the
  discount-curve input for the on-the-run Treasuries (#20-21).
* `credit_spread` — issuer Z-spread for corporate bonds (#15-19). Derives a 5Y par
  CDS spread from the well-populated credit-desk single-name feed
  (`credit/dtcc_single_name_cds.csv`, the same file the CDS trades #28-30 price
  off), lifts it to a Z-spread over SOFR via the SOFR-Treasury swap spread, and
  falls back to a configured per-issuer Z-spread only when that feed is too thin
  for the issuer (currently AAPL/MSFT/AMZN). The legacy, sparse bonds corp-CDS
  file is tried only as a secondary source.

Pure pandas/numpy — no QuantLib here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.common import storage
from src.common.sdr_prints import history_shortfall, price_forming_prints

from ..input.common.market_data import CreditSpreadData, TreasuryCurveData
from ..input.common.valuation import ValuationContext
from .snapshot import MarketDataSnapshot, SnapshotReader
from .staleness import pick_snapshot

# Treasury.gov column -> tenor label
_TSY_COLS = {
    "t1mo": "1M", "t2mo": "2M", "t3mo": "3M", "t4mo": "4M", "t6mo": "6M",
    "t1yr": "1Y", "t2yr": "2Y", "t3yr": "3Y", "t5yr": "5Y", "t7yr": "7Y",
    "t10yr": "10Y", "t20yr": "20Y", "t30yr": "30Y",
}
# tenors used to build the discount curve (skip the very-front bills; keep >=6M)
_TSY_CURVE_TENORS = ("6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y")

# Names that match an issuer keyword but are unrelated LBO entities in the SEC feed.
_CDS_NAME_EXCLUSIONS = ("BIDCO", "HOLDCO", "AVIATION", "AMENTUM", "ATLANTIC")


def _tenor_to_years(label: str) -> float:
    n, unit = float(label[:-1]), label[-1].upper()
    return n / 12.0 if unit == "M" else n


class BondsMarketDataAdapter(SnapshotReader):
    def __init__(self, config: dict, project_root: Path,
                 snapshot: MarketDataSnapshot | None = None):
        self._config = config                       # full config (for the SOFR curve)
        self.cfg = config.get("bonds", {})
        self.root = project_root
        self.data_dir = storage.join(storage.data_base(project_root), "marketdata", "bonds")
        self._snapshot = snapshot
        self._basis_cache: dict[date, dict[float, float]] = {}   # SOFR-Treasury basis bp curve per val date

    # ------------------------------------------------------------------ #
    # Treasury discount curve
    # ------------------------------------------------------------------ #
    def treasury_curve(self, ctx: ValuationContext) -> tuple[TreasuryCurveData, date, list[str]]:
        path = storage.join(self.data_dir, self.cfg.get("treasury_file", "treasury_par_yields.csv"))
        if not storage.exists(path):
            raise FileNotFoundError(f"Treasury par yields not found: {path}")

        df = self._read_csv(path, parse_dates=["date"])
        chosen, warnings = pick_snapshot(df["date"], ctx, label="Treasury par-yield",
                                         kind="treasury_curve")
        row = df[df["date"] == chosen].iloc[-1]
        as_of = chosen.date()

        tenors_cfg = tuple(self.cfg.get("treasury_tenors", _TSY_CURVE_TENORS))
        col_for = {v: k for k, v in _TSY_COLS.items()}
        tenors, yields = [], []
        for ten in tenors_cfg:
            col = col_for.get(ten)
            if col and col in row.index and pd.notna(row[col]):
                tenors.append(ten)
                yields.append(float(row[col]) / 100.0)   # percent -> decimal

        curve = TreasuryCurveData(
            currency="USD", as_of_date=as_of,
            tenors=tuple(tenors), par_yields=tuple(yields),
            source=f"treasury_gov_par_yields@{as_of.isoformat()}",
        )
        return curve, as_of, warnings

    # ------------------------------------------------------------------ #
    # Issuer credit spread (CDS-derived with config fallback)
    # ------------------------------------------------------------------ #
    def credit_spread(self, issuer: str, ctx: ValuationContext) -> tuple[CreditSpreadData, date, list[str]]:
        """Issuer Z-spread over SOFR for a corporate bond (#15-19).

        Sourcing order (2026-07-11 reconciliation):
          1. the well-populated CREDIT-desk single-name DTCC feed
             (``credit/dtcc_single_name_cds.csv`` — the same file the CDS trades
             #28-30 price off), converted from a 5Y par CDS spread to a Z-spread
             over SOFR by adding the SOFR-vs-Treasury swap spread;
          2. the legacy, sparse bonds corp-CDS file (kept only as a secondary
             source — it almost never yields a clean issuer spread);
          3. the per-issuer configured fallback Z-spread.
        """
        cs_cfg = self.cfg.get("credit_spread", {})
        recovery = float(cs_cfg.get("recovery", 0.40))
        as_of = ctx.valuation_date
        warnings: list[str] = self._cds_coverage_warnings(issuer, ctx, cs_cfg)

        # 1/2. Derive a 5Y par CDS spread (bp) from a DTCC feed, if clean.
        cds_bp, source = self._issuer_cds_bp(issuer, ctx, cs_cfg)
        if cds_bp is not None:
            # A CDS par spread is a credit spread over ~Treasury; the bond is
            # discounted on ZeroSpreadedTermStructure(SOFR, spread), so lift it to
            # a Z-spread over SOFR by adding the (positive) SOFR-Treasury basis,
            # computed from the free SOFR + Treasury curves (not a hardcoded const).
            basis_bp = self._sofr_treasury_basis_bp(ctx)
            spread = (cds_bp + basis_bp) / 1e4
            # A3: build a Z-spread term structure where the CDS feed has >=2 clean tenors
            # (currently ORCL). Each tenor's Z-spread = its CDS par spread + the SOFR-vs-
            # Treasury basis at that tenor (maturity-interpolated, not one 10Y point). The
            # 5Y node is pinned to the headline spread. <2 tenors => flat (empty curve).
            curve_points = self._issuer_zspread_curve(issuer, ctx, cs_cfg, primary=(5.0, spread))
            return (
                CreditSpreadData(issuer=issuer, currency="USD", spread=spread,
                                 as_of_date=as_of, recovery=recovery, from_cds=True,
                                 source=source, curve_points=curve_points),
                as_of, warnings,
            )

        # 3. Config fallback (already a Z-spread over SOFR).
        fallback_bp = cs_cfg.get("issuer_fallback_bp", {})
        if issuer not in fallback_bp:
            raise KeyError(
                f"No usable CDS spread for {issuer!r} and no config fallback "
                f"(bonds.credit_spread.issuer_fallback_bp). Add one."
            )
        spread = float(fallback_bp[issuer]) / 1e4
        warnings.append(
            f"No clean DTCC CDS spread for {issuer}; using configured fallback "
            f"{fallback_bp[issuer]:.0f}bp (free single-name CDS feed too thin for this issuer)."
        )
        return (
            CreditSpreadData(issuer=issuer, currency="USD", spread=spread,
                             as_of_date=as_of, recovery=recovery, from_cds=False,
                             source="config-fallback"),
            as_of, warnings,
        )

    def _basis_curve(self, ctx: ValuationContext) -> dict[float, float]:
        """SOFR-vs-Treasury basis (bp) by tenor-years, from FREE curves.

        (Treasury.gov par minus Pensford SOFR-OIS par) at every shared tenor — computed
        live from the two curves the pricer already loads. Cached per valuation date and
        clamped to a sane band. Empty dict if the curves are unavailable.
        """
        vd = ctx.valuation_date
        if vd in self._basis_cache:
            return self._basis_cache[vd]

        cs_cfg = self.cfg.get("credit_spread", {})
        lo = float(cs_cfg.get("basis_min_bp", 0.0))
        hi = float(cs_cfg.get("basis_max_bp", 100.0))
        curve: dict[float, float] = {}
        try:
            from .rates_adapter import RatesMarketDataAdapter
            tsy_curve, _t, _tw = self.treasury_curve(ctx)
            ois_curve, _o, _ow = RatesMarketDataAdapter(
                self._config, self.root, self._snapshot).par_ois_curve(ctx)
            tsy = dict(zip(tsy_curve.tenors, tsy_curve.par_yields, strict=True))
            ois = dict(zip(ois_curve.tenors, ois_curve.par_rates, strict=True))
            for ten in set(tsy) & set(ois):
                curve[_tenor_to_years(ten)] = min(max((tsy[ten] - ois[ten]) * 1e4, lo), hi)
        except Exception:  # never fail pricing on the basis — leave it empty
            curve = {}

        self._basis_cache[vd] = curve
        return curve

    def _basis_at(self, ctx: ValuationContext, tenor_y: float) -> float:
        """SOFR-vs-Treasury basis (bp) at `tenor_y` years, linearly interpolated across the
        basis curve (flat-clamped outside). Falls back to the configured constant when the
        free curves are unavailable."""
        cs_cfg = self.cfg.get("credit_spread", {})
        fallback = float(cs_cfg.get("sofr_treasury_swap_spread_bp", 45.0))
        curve = self._basis_curve(ctx)
        if not curve:
            return fallback
        xs = sorted(curve)
        ys = [curve[x] for x in xs]
        if tenor_y <= xs[0]:
            return ys[0]
        if tenor_y >= xs[-1]:
            return ys[-1]
        import numpy as np
        return float(np.interp(tenor_y, xs, ys))

    def _sofr_treasury_basis_bp(self, ctx: ValuationContext) -> float:
        """Headline SOFR-vs-Treasury basis (bp) added to the 5Y CDS spread to lift it to a
        Z-spread over SOFR. Uses the basis at the configured representative tenor (default
        10Y — the two CDS-derived issuers, ORCL 2053 / META 2054, are long-dated)."""
        cs_cfg = self.cfg.get("credit_spread", {})
        return self._basis_at(ctx, _tenor_to_years(str(cs_cfg.get("basis_tenor", "10Y"))))

    def _issuer_cds_bp(self, issuer: str, ctx: ValuationContext,
                       cs_cfg: dict) -> tuple[float | None, str]:
        """5Y par CDS spread (bp) for the issuer, and the source tag.

        Reads the credit-desk single-name feed (the retired bonds corp-CDS feed held no new
        trades; credit-sdr-dedup). Returns (None, "") when it yields no clean spread, and the
        caller falls back to the configured spread.
        """
        bp = self._spread_from_single_name_cds(issuer, ctx, cs_cfg)
        if bp is not None:
            return bp, "dtcc_single_name"
        return None, ""

    def _single_name_records(self, cs_cfg: dict) -> pd.DataFrame | None:
        """The DTCC single-name CDS file (every value a string), or None when it's absent."""
        path = storage.join(storage.data_base(self.root), "marketdata", "credit",
                            cs_cfg.get("single_name_cds_file", "dtcc_single_name_cds.csv"))
        if not storage.exists(path):
            return None
        return self._read_csv(path, dtype=str, keep_default_na=False, na_values=[""])

    def _cds_coverage_warnings(self, issuer: str, ctx: ValuationContext, cs_cfg: dict) -> list[str]:
        """A warning when the issuer reads the CDS feed and its history starts too late.

        Args:
            issuer: Issuer ticker.
            ctx: Valuation context.
            cs_cfg: The ``bonds.credit_spread`` config.

        Returns:
            One warning, or none (also none for issuers priced from the config fallback only).
        """
        if not cs_cfg.get("issuer_cds_names", {}).get(issuer):
            return []
        df = self._single_name_records(cs_cfg)
        lookback = int(cs_cfg.get("curve_lookback_days", 365))
        first = None if df is None else history_shortfall(df, ctx.valuation_date.isoformat(), lookback)
        if first is None:
            return []
        return [f"{issuer}: DTCC credit history starts {first}, after the {lookback}d window this valuation "
                f"needs; spreads use fewer prints (raise keep_from_date in configs/marketdata/credit and "
                f"re-ingest)."]

    def _issuer_clean_quotes(self, issuer: str, ctx: ValuationContext, cs_cfg: dict,
                             lookback: int) -> pd.DataFrame | None:
        """Name-matched, in-window, band-filtered single-name CDS quotes for the issuer
        (`sp` decimal + `Expiration Date`) — the pool both the 5Y headline and the
        term-structure buckets draw from. None if the feed/names are unusable."""
        names = [n.upper() for n in cs_cfg.get("issuer_cds_names", {}).get(issuer, [])]
        if not names:
            return None
        df = self._single_name_records(cs_cfg)
        if df is None:
            return None
        if "Underlying Asset Name" not in df.columns or "Spread-Leg 1" not in df.columns:
            return None

        # Only new trades count, windowed by execution date, nothing disseminated after the
        # valuation date (credit-sdr-dedup).
        sub = price_forming_prints(df, ctx.valuation_date.isoformat(), lookback)
        name_u = sub["Underlying Asset Name"].fillna("").astype(str).str.upper()
        match = name_u.apply(lambda s: any(n in s for n in names)
                             and not any(x in s for x in _CDS_NAME_EXCLUSIONS)).astype(bool)
        sub = sub[match].copy()
        sub["Expiration Date"] = pd.to_datetime(sub["Expiration Date"], errors="coerce")
        sub["sp"] = pd.to_numeric(sub["Spread-Leg 1"], errors="coerce")
        min_s = float(cs_cfg.get("min_bp", 5)) / 1e4
        max_s = float(cs_cfg.get("max_bp", 500)) / 1e4
        sub = sub[(sub["sp"] >= min_s) & (sub["sp"] <= max_s)]
        return sub if not sub.empty else None

    @staticmethod
    def _median_bucket(sub: pd.DataFrame, vts: pd.Timestamp, tenor_y: int,
                       tol: int, min_obs: int) -> float | None:
        """Median par spread (bp) of quotes whose expiry sits within `tol` days of the
        standard-roll maturity for `tenor_y`. None if too thin."""
        target = vts + pd.DateOffset(years=tenor_y)
        bucket = sub[(sub["Expiration Date"] - target).abs().dt.days <= tol]
        if len(bucket) < min_obs:
            return None
        return float(bucket["sp"].median()) * 1e4

    def _spread_from_single_name_cds(self, issuer: str, ctx: ValuationContext,
                                     cs_cfg: dict) -> float | None:
        """Median 5Y par CDS spread (bp) for the issuer (tight window / stricter obs bar)."""
        sub = self._issuer_clean_quotes(issuer, ctx, cs_cfg,
                                        int(cs_cfg.get("single_name_lookback_days", 120)))
        if sub is None:
            return None
        return self._median_bucket(
            sub, pd.Timestamp(ctx.valuation_date),
            int(cs_cfg.get("single_name_tenor_years", 5)),
            int(cs_cfg.get("single_name_tenor_tolerance_days", 120)),
            int(cs_cfg.get("min_observations", 3)))

    def _issuer_zspread_curve(self, issuer: str, ctx: ValuationContext, cs_cfg: dict,
                              primary: tuple[float, float]) -> tuple[tuple[float, float], ...]:
        """A3: issuer Z-spread term structure = per-tenor CDS spread + maturity-interpolated
        SOFR-Treasury basis, over the standard tenor grid (wider window / lower obs bar than
        the 5Y headline). The 5Y node is pinned to `primary` (tenor_years, z_spread_decimal).
        Returns ascending (tenor_years, z_spread_decimal) with >=2 points, else ()."""
        sub = self._issuer_clean_quotes(issuer, ctx, cs_cfg,
                                        int(cs_cfg.get("curve_lookback_days", 365)))
        grid = cs_cfg.get("curve_tenors_years", [1, 2, 3, 5, 7, 10])
        tol = int(cs_cfg.get("single_name_tenor_tolerance_days", 120))
        cmin = int(cs_cfg.get("curve_min_observations", 2))
        vts = pd.Timestamp(ctx.valuation_date)
        pts: dict[float, float] = {}
        if sub is not None:
            for t in grid:
                cds_bp = self._median_bucket(sub, vts, int(t), tol, cmin)
                if cds_bp is not None:
                    pts[float(t)] = (cds_bp + self._basis_at(ctx, float(t))) / 1e4
        pts[primary[0]] = primary[1]                 # pin 5Y to the headline Z-spread
        return tuple(sorted(pts.items())) if len(pts) >= 2 else ()
