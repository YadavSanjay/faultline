"""Credit market-data adapter.

Extracts the 5Y par CDS spread for a reference entity (#28 ORCL, #29 META) or
index (#30 CDX.NA.IG) from the DTCC dissemination:

  * single names -> `dtcc_single_name_cds.csv` (SEC path, `Underlying Asset Name`)
  * indices      -> `dtcc_index_cds.csv` (CFTC path, `UPI Underlier Name`)

Filters to the reference's names (excluding LBO false positives), recent trades
(lookback window) in the ~5Y tenor bucket, and takes the median running spread.
Unlike the bonds corp-CDS file, this feed is well populated (ORCL ~546 / CDX ~184k
usable rows), so DTCC is primary; a config fallback covers thin names. Pure pandas.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.common import storage
from src.common.sdr_prints import history_shortfall, price_forming_prints

from ..input.common.market_data import CdsSpreadData
from ..input.common.valuation import ValuationContext
from .snapshot import MarketDataSnapshot, SnapshotReader


class CreditMarketDataAdapter(SnapshotReader):
    def __init__(self, config: dict, project_root: Path,
                 snapshot: MarketDataSnapshot | None = None):
        self.cfg = config.get("credit", {})
        self.root = project_root
        self.data_dir = storage.join(storage.data_base(project_root), "marketdata", "credit")
        self._snapshot = snapshot

    def cds_spread(self, reference: str, ctx: ValuationContext) -> tuple[CdsSpreadData, date, list[str]]:
        refs = self.cfg.get("references", {})
        if reference not in refs:
            raise KeyError(f"No credit config for reference {reference!r} (credit.references)")
        rcfg = refs[reference]
        recovery = float(rcfg.get("recovery", self.cfg.get("recovery", 0.40)))
        as_of = ctx.valuation_date
        warnings: list[str] = []
        # B4: index mechanics. index_factor is the outstanding-notional fraction (1.0 for an
        # on-the-run series with no defaulted names; the free feed carries no default data);
        # n_constituents is the series size, reported so the index-level model is explicit.
        is_index = rcfg.get("kind", "single_name") == "index"
        index_factor = float(rcfg.get("index_factor", 1.0)) if is_index else 1.0
        n_constituents = int(rcfg.get("n_constituents", self.cfg.get("index_size", 0))) or None if is_index else None
        primary_tenor = int(self.cfg.get("tenor_years", 5))
        tol = int(self.cfg.get("tenor_tolerance_days", 120))
        min_obs = int(self.cfg.get("min_observations", 3))

        vts = pd.Timestamp(ctx.valuation_date)
        grid = self.cfg.get("curve_tenors_years", [1, 2, 3, 5, 7, 10])
        cmin = int(self.cfg.get("curve_min_observations", 2))
        warnings += self._coverage_warnings(rcfg, ctx, int(self.cfg.get("curve_lookback_days", 365)))
        # 5Y headline: tight window / stricter obs bar (drives the reported par spread and
        # the fair-spread==par-spread IPV round-trip, and the corp-bond spread lookup).
        tight = self._clean_quotes(rcfg, ctx, int(self.cfg.get("lookback_days", 120)))
        spread = None if tight is None else self._median_in_bucket(tight, vts, primary_tenor, tol, min_obs)
        headline_is_5y = spread is not None
        # B5: if the tight 5Y bucket is thin, widen before giving up to the config fallback —
        # try 5Y in the wider window, then the nearest populated standard tenor as a proxy.
        wide = self._clean_quotes(rcfg, ctx, int(self.cfg.get("curve_lookback_days", 365)))
        if spread is None and wide is not None:
            spread = self._median_in_bucket(wide, vts, primary_tenor, tol, cmin)
            if spread is not None:
                headline_is_5y = True
                warnings.append(f"{reference}: 5Y CDS thin in the tight window; used the wider "
                                f"{self.cfg.get('curve_lookback_days', 365)}d window for the 5Y headline.")
            else:
                for t in sorted(grid, key=lambda x: abs(int(x) - primary_tenor)):
                    s = self._median_in_bucket(wide, vts, int(t), tol, cmin)
                    if s is not None:
                        spread = s
                        warnings.append(f"{reference}: no clean 5Y CDS; used the nearest tenor "
                                        f"{int(t)}Y as the headline spread proxy.")
                        break
        if spread is not None:
            # A2: build a term structure of par spreads (single names only — an index is
            # priced flat). The free DTCC feed trades mostly the on-the-run 5Y, so the
            # non-5Y buckets draw from a wider window / lower obs bar; the 5Y node is
            # pinned to the headline `spread`. <2 qualifying tenors => stay flat. Only build a
            # curve when the headline is a genuine 5Y (not a nearest-tenor proxy).
            curve_points: tuple[tuple[float, float], ...] = ()
            if not is_index and headline_is_5y and wide is not None:
                pts = {}
                for t in grid:
                    s = self._median_in_bucket(wide, vts, int(t), tol, cmin)
                    if s is not None:
                        pts[float(t)] = s
                pts[float(primary_tenor)] = spread          # pin 5Y to the headline
                if len(pts) >= 2:
                    curve_points = tuple(sorted(pts.items()))
            return (
                CdsSpreadData(reference=reference, currency="USD", spread=spread,
                              recovery=recovery, as_of_date=as_of,
                              tenor=f"{primary_tenor}Y", from_dtcc=True, source="dtcc",
                              curve_points=curve_points, index_factor=index_factor,
                              n_constituents=n_constituents),
                as_of, warnings,
            )

        if "fallback_bp" not in rcfg:
            raise KeyError(f"No usable DTCC CDS spread for {reference!r} and no config fallback")
        spread = float(rcfg["fallback_bp"]) / 1e4
        warnings.append(
            f"No usable DTCC CDS quotes for {reference} in window; using configured "
            f"fallback {rcfg['fallback_bp']:.0f}bp."
        )
        return (
            CdsSpreadData(reference=reference, currency="USD", spread=spread,
                          recovery=recovery, as_of_date=as_of,
                          tenor=f"{primary_tenor}Y", from_dtcc=False, source="config-fallback",
                          index_factor=index_factor, n_constituents=n_constituents),
            as_of, warnings,
        )

    def _records(self, rcfg: dict) -> tuple[pd.DataFrame | None, str]:
        """The reference's DTCC credit file (every value a string) and its name column."""
        if rcfg.get("kind", "single_name") == "index":
            fname = self.cfg.get("index_file", "dtcc_index_cds.csv")
            namecol = "UPI Underlier Name"
        else:
            fname = self.cfg.get("single_name_file", "dtcc_single_name_cds.csv")
            namecol = "Underlying Asset Name"
        path = storage.join(self.data_dir, fname)
        if not storage.exists(path):
            return None, namecol
        return self._read_csv(path, dtype=str, keep_default_na=False, na_values=[""]), namecol

    def _coverage_warnings(self, rcfg: dict, ctx: ValuationContext, lookback: int) -> list[str]:
        """A warning when the stored history starts after the window this valuation needs.

        Args:
            rcfg: The reference's config.
            ctx: Valuation context.
            lookback: The longest calendar-day window the spreads draw from.

        Returns:
            One warning, or none.
        """
        df, _ = self._records(rcfg)
        first = None if df is None else history_shortfall(df, ctx.valuation_date.isoformat(), lookback)
        if first is None:
            return []
        return [f"DTCC credit history starts {first}, after the {lookback}d window this valuation needs; "
                f"spreads use fewer prints (raise keep_from_date in configs/marketdata/credit and re-ingest)."]

    def _clean_quotes(self, rcfg: dict, ctx: ValuationContext, lookback: int) -> pd.DataFrame | None:
        """Returns the name-matched, in-window, spread-sane price-forming CDS quotes.

        The shared pool both the 5Y bucket and the term-structure buckets draw from, with numeric
        ``sp`` (decimal) and parsed ``Expiration Date``. ``lookback`` is passed in so the tight 5Y
        headline and the wider term-structure build can use different windows.

        Args:
            rcfg: The reference's config (``kind``, ``names``, ``exclude``).
            ctx: Valuation context.
            lookback: Calendar days before the valuation date.

        Returns:
            The quotes, or None when the file is missing or nothing qualifies.
        """
        df, namecol = self._records(rcfg)
        if df is None:
            return None
        if namecol not in df.columns or "Spread-Leg 1" not in df.columns:
            return None

        # Only new trades count (not lifecycle, novation, clearing or correction records), windowed
        # by execution date, and nothing disseminated after the valuation date (credit-sdr-dedup).
        sub = price_forming_prints(df, ctx.valuation_date.isoformat(), lookback)

        names = [n.upper() for n in rcfg.get("names", [])]
        excl = [x.upper() for x in rcfg.get("exclude", [])]
        nm = sub[namecol].fillna("").astype(str).str.upper()
        match = nm.apply(lambda s: any(n in s for n in names) and not any(x in s for x in excl)).astype(bool)
        sub = sub[match].copy()
        sub["Expiration Date"] = pd.to_datetime(sub["Expiration Date"], errors="coerce")

        sub["sp"] = pd.to_numeric(sub["Spread-Leg 1"], errors="coerce")
        min_s = float(self.cfg.get("min_bp", 5)) / 1e4
        max_s = float(self.cfg.get("max_bp", 3000)) / 1e4
        sub = sub[(sub["sp"] >= min_s) & (sub["sp"] <= max_s)]
        return sub if not sub.empty else None

    @staticmethod
    def _median_in_bucket(sub: pd.DataFrame, vts: pd.Timestamp, tenor_y: int,
                          tol: int, min_obs: int) -> float | None:
        """Median running spread of the quotes whose expiry sits within `tol` days of
        `vts + tenor_y` (the standard-roll maturity for that tenor). None if too thin."""
        target = vts + pd.DateOffset(years=tenor_y)
        bucket = sub[(sub["Expiration Date"] - target).abs().dt.days <= tol]
        if len(bucket) < min_obs:
            return None
        return float(bucket["sp"].median())
