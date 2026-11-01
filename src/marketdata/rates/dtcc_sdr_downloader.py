"""
DTCC/CFTC SDR RATES cumulative-file downloader.

Downloads CFTC_CUMULATIVE_RATES_{YYYY}_{MM}_{DD}.zip from S3 (no auth).
Splits each day's CSV into two output files:
  - dtcc_sdr_irs.csv      — every USD SOFR swap record, any action type (for par-rate derivation)
  - dtcc_sdr_ir_options.csv — swaption / cap / floor trades (for implied-vol proxy)

Records are keyed on the CFTC Dissemination Identifier and merged with Part 43 corrections and
cancellations applied (src/marketdata/rates/sdr_records.py; spec docs/specs/sdr-dedup.md). Every
value is read and written as an exact string. A legacy file (the old one-row-per-day layout, no
Dissemination Identifier) is replaced and its whole window re-fetched. Cancelled chain roots are
kept in a `<file>.cancelled.csv` sidecar so a day fetched late can't bring a cancelled trade back.

Data available from 2023-12-29 (DTCC SDR redesign). The window is start_date_override, narrowed
to the last lookback_business_days when set; records older than the window are dropped, so the
files stay a rolling window (~5.7k SOFR records per day).

Incremental: already-downloaded business dates are skipped on re-runs.

Par-swap-rate derivation (post-processing — not done here):
    Group IRS records by tenor bucket (from Expiration Date - Effective Date).
    Compute median of Fixed rate-Leg 1 per tenor → daily par SOFR OIS par rates.
    These are the "raw" obs fed into the SOFR OIS curve bootstrap as a cross-check
    against Pensford published nodes.
"""

import io
import logging
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..common.data_store import get_output_path, write_provenance
from ..common.http_client import HttpClient
from .sdr_records import (
    cancelled_sidecar,
    filter_sofr_swap_records,
    merge_sdr_records,
    read_sdr_records,
    roll_sdr_window,
    sdr_dates_to_fetch,
    write_sdr_records,
)

logger = logging.getLogger(__name__)

# Minimum columns we expect in a CFTC RATES slice (Appendix A Phase 2 layout)
_EXPECTED_COLS = {
    "Dissemination Identifier",
    "Action type",
    "Asset Class",
    "Product name",
    "Execution Timestamp",
    "Effective Date",
    "Expiration Date",
    "Fixed rate-Leg 1",
    "Spread-Leg 1",
    "Notional amount-Leg 1",
    "Notional currency-Leg 1",
    "Underlying Asset Name",
    "Option Premium Amount",
    "First exercise date",
    "Maturity date of the underlier",
}


def _business_dates(start: str, end: str) -> list[str]:
    """Return all Mon-Fri dates between start and end (inclusive)."""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    dates = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:  # 0=Mon … 4=Fri
            dates.append(cur.isoformat())
        cur += timedelta(days=1)
    return dates


class DTCCSDRRatesDownloader:
    """
    Download DTCC CFTC RATES cumulative ZIPs and split into IRS + options CSVs.
    """

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["dtcc_sdr_rates"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        # Use the SDR-specific start override (2024-01-01), not the global 2022 start
        self._start = src_cfg.get("start_date_override", config["time_window"]["start_date"])
        self._end = config["time_window"]["end_date"]
        lookback = src_cfg.get("lookback_business_days")
        if lookback:
            window = _business_dates(self._start, self._end)[-int(lookback):]
            if window:
                self._start = window[0]
        self._client = HttpClient(src_cfg, credentials)

        self._irs_path = get_output_path(self._asset_class, src_cfg["output_files"]["irs"])
        self._opt_path = get_output_path(self._asset_class, src_cfg["output_files"]["options"])

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Fetches the missing business days and merges them into both output files.

        Returns:
            The newly fetched (IRS, options) records, before merging.
        """
        all_dates = _business_dates(self._start, self._end)
        existing_irs = read_sdr_records(self._irs_path)
        existing_opt = read_sdr_records(self._opt_path)
        # A date is fetched when either file holds records for it (a day may carry swaps but no
        # options, or the reverse). A legacy file in either place forces a full re-fetch.
        legacy = any(not df.empty and "Dissemination Identifier" not in df.columns
                     for df in (existing_irs, existing_opt))
        missing = set(all_dates) if legacy else (
            set(sdr_dates_to_fetch(existing_irs, all_dates)) & set(sdr_dates_to_fetch(existing_opt, all_dates)))
        to_fetch = [d for d in all_dates if d in missing]
        logger.info(
            "DTCC SDR RATES: %d business days in window (from %s), %d to fetch",
            len(all_dates), self._start, len(to_fetch),
        )

        irs_frames, opt_frames = [], []
        for d in to_fetch:
            irs_df, opt_df = self._download_day(d)
            if not irs_df.empty:
                irs_frames.append(irs_df)
            if not opt_df.empty:
                opt_frames.append(opt_df)

        new_irs = pd.concat(irs_frames, ignore_index=True) if irs_frames else pd.DataFrame()
        new_opt = pd.concat(opt_frames, ignore_index=True) if opt_frames else pd.DataFrame()
        self._store(existing_irs, new_irs, self._irs_path)
        self._store(existing_opt, new_opt, self._opt_path)

        logger.info("DTCC SDR RATES: done. IRS→%s  Options→%s", self._irs_path, self._opt_path)
        return new_irs, new_opt

    def _store(self, existing: pd.DataFrame, new: pd.DataFrame, path: str | Path) -> None:
        """Merges `new` into `existing`, drops records older than the window, and writes the file.

        Nothing is written when nothing new was fetched, so a failed run never replaces the file
        (including a legacy one) with less data.

        Args:
            existing: Records already on disk (may be empty or legacy).
            new: Records fetched this run.
            path: Output location.
        """
        if new.empty:
            return
        cancelled_path = cancelled_sidecar(path)
        merged, cancelled = roll_sdr_window(existing, new, read_sdr_records(cancelled_path), self._start,
                                            merge_sdr_records)
        write_sdr_records(merged, path)
        write_provenance(path, merged)
        write_sdr_records(cancelled, cancelled_path)
        logger.info("DTCC SDR RATES: %d records (%d cancelled roots) -> %s", len(merged), len(cancelled), path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _download_day(self, day: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        date_tag = day.replace("-", "_")
        file_path = self._cfg["file_pattern"].format(date=date_tag)

        try:
            resp = self._client.get(file_path)
        except Exception as exc:
            logger.debug("DTCC SDR RATES %s: not available (%s)", day, exc)
            return pd.DataFrame(), pd.DataFrame()

        try:
            raw_df = self._unzip_and_parse(resp.content, day)
        except Exception as exc:
            logger.warning("DTCC SDR RATES %s: parse error: %s", day, exc)
            return pd.DataFrame(), pd.DataFrame()

        if raw_df.empty:
            return pd.DataFrame(), pd.DataFrame()

        irs_df = self._filter_irs(raw_df, day)
        opt_df = self._filter_options(raw_df, day)
        logger.info("DTCC SDR RATES %s: %d IRS rows, %d option rows", day, len(irs_df), len(opt_df))
        return irs_df, opt_df

    def _unzip_and_parse(self, content: bytes, day: str) -> pd.DataFrame:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                logger.warning("DTCC SDR RATES %s: ZIP contains no CSV", day)
                return pd.DataFrame()
            with zf.open(csv_names[0]) as fh:
                # Every value as an exact string: 19-digit identifiers don't survive a numeric parse.
                df = pd.read_csv(fh, dtype=str, keep_default_na=False, na_values=[""], low_memory=False)

        # Validate that we got the right kind of file
        present = set(df.columns)
        if not _EXPECTED_COLS.issubset(present):
            missing = _EXPECTED_COLS - present
            logger.warning(
                "DTCC SDR RATES %s: missing expected columns: %s "
                "(schema may have changed — check PPD User Guide)",
                day, missing,
            )
        return df

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def _filter_irs(self, df: pd.DataFrame, day: str) -> pd.DataFrame:
        """Keeps every USD SOFR swap record (any action type) for par-swap-rate derivation.

        In the CFTC RATES public dissemination (Part 43), 'Product name' and 'Underlying Asset
        Name' are empty; 'UPI Underlier Name' carries the reference rate (e.g.
        "USD-SOFR-OIS Compound"). Rows with an 'Option Premium Amount' are options and excluded.
        Corrections and cancellations are kept so the merge can apply them.

        Args:
            df: One day's raw records.
            day: The business date.

        Returns:
            The kept records with the sdr_records output columns.
        """
        cfg_f = self._cfg["filters"]
        return filter_sofr_swap_records(df, day, list(cfg_f["upi_underlier_keywords"]), cfg_f["currency"])

    def _filter_options(self, df: pd.DataFrame, day: str) -> pd.DataFrame:
        """
        Keep swaption / cap / floor trades for implied-vol derivation.
        These have a non-null 'Option Premium Amount'.
        """
        cfg_f = self._cfg["filters"]
        # Every action type, as for swaps, so the merge can apply corrections and cancellations.
        mask = (
            self._col_contains(df, "Notional currency-Leg 1", [cfg_f["currency"]]) &
            self._col_contains(df, "UPI Underlier Name", cfg_f["upi_underlier_keywords"])
        )
        opts = df[mask].copy()
        if opts.empty:
            return opts

        # Keep only rows with an option premium (confirms it's an option product)
        if "Option Premium Amount" in opts.columns:
            opts = opts[opts["Option Premium Amount"].notna()]

        if opts.empty:
            return opts
        opts = self._add_date_col(opts, day)
        return self._select_option_cols(opts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _col_contains(df: pd.DataFrame, col: str, keywords: list[str]) -> pd.Series:
        if col not in df.columns:
            return pd.Series(False, index=df.index)
        pattern = "|".join(re.escape(k) for k in keywords)
        return df[col].astype(str).str.contains(pattern, case=False, na=False, regex=True)

    @staticmethod
    def _add_date_col(df: pd.DataFrame, day: str) -> pd.DataFrame:
        df = df.copy()
        df["date"] = day
        return df

    @staticmethod
    def _filter_spot_starting(df: pd.DataFrame, max_lag_days: int) -> pd.DataFrame:
        if "Effective Date" not in df.columns or "Execution Timestamp" not in df.columns:
            return df
        eff = pd.to_datetime(df["Effective Date"], errors="coerce", utc=True)
        exe = pd.to_datetime(df["Execution Timestamp"], errors="coerce", utc=True).dt.normalize()
        lag = (eff - exe).dt.days.abs()
        return df[lag <= max_lag_days]

    @staticmethod
    def _select_option_cols(df: pd.DataFrame) -> pd.DataFrame:
        want = [
            "date", "Dissemination Identifier", "Original Dissemination Identifier",
            "Action type", "Event type", "Event timestamp",
            "Execution Timestamp", "Effective Date",
            "Expiration Date", "Maturity date of the underlier",
            "UPI FISN", "UPI Underlier Name", "Option Type", "Option Style", "Strike Price",
            "Fixed rate-Leg 1", "Fixed rate-Leg 2", "Spread-Leg 1",
            "Option Premium Amount", "Option Premium Currency",
            "First exercise date",
            "Notional amount-Leg 1",
        ]
        return df[[c for c in want if c in df.columns]].copy()


