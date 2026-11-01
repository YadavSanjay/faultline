"""DTCC SDR CREDITS downloader — single-name and index CDS for trades #28-30.

Two public-dissemination populations live behind the same daily cumulative-ZIP
format on S3 but in different jurisdiction prefixes:

  sec/eod/SEC_CUMULATIVE_CREDITS_{date}.zip
      US single-name CDS (security-based swaps, SEC jurisdiction).
      'Underlying Asset Name' IS populated ("Oracle Corporation",
      "META PLATFORMS INC.", ...). Used for trades #28 (ORCL) and #29 (META) and the
      ORCL/META bond spreads (#17/#18).

  cftc/eod/CFTC_CUMULATIVE_CREDITS_{date}.zip
      Index CDS (CDX, iTraxx — CFTC jurisdiction).
      'Underlying Asset Name' is EMPTY; the index name lives in
      'UPI Underlier Name' ("CDX.NA.IG", "CDX.NA.HY"). Used for trade #30.

Both share one flow (spec docs/specs/credit-sdr-dedup.md): every record of the configured names is
kept, for every action type, keyed on the CFTC Dissemination Identifier, with Part 43 corrections
and cancellations applied (src/marketdata/rates/sdr_records.py). Every value is read and written as
an exact string. A legacy file (the old one-row-per-day layout) is replaced and its window
re-fetched; records older than the window (lookback_business_days) are dropped; cancelled trades
are remembered in a `<file>.cancelled.csv` sidecar.

The files hold every record, not only prices: the consumers select price-forming prints (new
trades, Event type TRAD) with src/marketdata/credit/sdr_credit_records.price_forming_prints.

Spreads: the running CDS spread is in 'Spread-Leg 1' as a DECIMAL fraction (0.01535 = 153.5 bp).
It is frequently blank on individual prints — the standard 100 bp IG coupon appears in
'Fixed rate-Leg 1' and the economics may be carried as upfront 'Price' instead.

Incremental: already-downloaded business dates are skipped on re-runs.
"""

import io
import logging
import zipfile
from datetime import date, timedelta

import pandas as pd

from ..common.data_store import get_output_path, write_provenance
from ..common.http_client import HttpClient
from ..rates.sdr_records import (
    cancelled_sidecar,
    merge_sdr_chains,
    read_sdr_records,
    roll_sdr_window,
    sdr_dates_to_fetch,
    write_sdr_records,
)
from .sdr_credit_records import filter_credit_records

logger = logging.getLogger(__name__)


def _business_dates(start: str, end: str) -> list[str]:
    """Return all Mon-Fri dates between start and end (inclusive)."""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


class _DTCCCreditDownloader:
    """Base downloader for a single DTCC CREDITS source (SEC or CFTC path)."""

    # Subclasses set the config source key.
    SOURCE_KEY: str = ""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"][self.SOURCE_KEY]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config.get("dtcc_start_date", config["time_window"]["start_date"])
        self._end = config["time_window"]["end_date"]
        # History is kept from an absolute date, not a window counted back from today: the pricer
        # looks back curve_lookback_days from the *valuation* date, so a run-date-relative window
        # would quietly thin out pinned valuation dates as time passes. keep_from_date must cover
        # every pinned valuation date minus that lookback (tests/pricer/test_credit.py checks it).
        keep_from = src_cfg.get("keep_from_date")
        lookback = src_cfg.get("lookback_business_days")
        if keep_from:
            self._start = max(self._start, str(keep_from))
        elif lookback:
            window = _business_dates(self._start, self._end)[-int(lookback):]
            if window:
                self._start = window[0]
        self._client = HttpClient(src_cfg, credentials)

        self._filter_col = src_cfg["filter_column"]
        self._include = list(src_cfg["filter_keywords"])
        self._exclude = list(src_cfg.get("exclude_keywords") or [])

        self._out_path = get_output_path(self._asset_class, src_cfg["output_file"])

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> pd.DataFrame:
        """Fetches the missing business days and merges them into the output file.

        Returns:
            The newly fetched records, before merging.
        """
        all_dates = _business_dates(self._start, self._end)
        existing = read_sdr_records(self._out_path)
        to_fetch = sdr_dates_to_fetch(existing, all_dates)
        logger.info("%s: %d business days in window (from %s), %d to fetch",
                    self.SOURCE_KEY, len(all_dates), self._start, len(to_fetch))

        frames = []
        for day in to_fetch:
            df = self._download_day(day)
            if not df.empty:
                frames.append(df)
        if not frames:
            # Nothing written: a failed run never replaces the file (including a legacy one).
            logger.info("%s: no new data (all dates downloaded or unavailable)", self.SOURCE_KEY)
            return pd.DataFrame()

        new = pd.concat(frames, ignore_index=True)
        cancelled_path = cancelled_sidecar(self._out_path)
        merged, cancelled = roll_sdr_window(existing, new, read_sdr_records(cancelled_path), self._start,
                                            merge_sdr_chains)
        write_sdr_records(merged, self._out_path)
        write_provenance(self._out_path, merged)
        write_sdr_records(cancelled, cancelled_path)
        logger.info("%s: %d new records; %d records (%d cancelled roots) -> %s",
                    self.SOURCE_KEY, len(new), len(merged), len(cancelled), self._out_path)
        return new

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _download_day(self, day: str) -> pd.DataFrame:
        """Fetches one day's cumulative file and keeps the configured names' records.

        Args:
            day: The business date (``YYYY-MM-DD``).

        Returns:
            The kept records (empty when the file is unavailable or unparseable).
        """
        date_tag = day.replace("-", "_")
        file_path = self._cfg["file_pattern"].format(date=date_tag)

        try:
            resp = self._client.get(file_path)
        except Exception as exc:  # isolation point: one missing day never stops the run
            logger.debug("%s %s: not available (%s)", self.SOURCE_KEY, day, exc)
            return pd.DataFrame()

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
                if not csv_names:
                    return pd.DataFrame()
                # Every value as an exact string: 19-digit identifiers don't survive a numeric parse.
                df = pd.read_csv(zf.open(csv_names[0]), dtype=str, keep_default_na=False, na_values=[""],
                                 low_memory=False)
        except Exception as exc:  # isolation point: a corrupt day is logged and skipped
            logger.warning("%s %s: parse error: %s", self.SOURCE_KEY, day, exc)
            return pd.DataFrame()

        if self._filter_col not in df.columns:
            logger.warning("%s %s: filter column %r missing", self.SOURCE_KEY, day, self._filter_col)
            return pd.DataFrame()

        result = filter_credit_records(df, day, self._filter_col, self._include, self._exclude)
        logger.info("%s %s: %d records", self.SOURCE_KEY, day, len(result))
        return result


class DTCCSingleNameDownloader(_DTCCCreditDownloader):
    """Single-name CDS (ORCL, META) from the SEC SDR path — trades #28-29."""
    SOURCE_KEY = "dtcc_single_name"


class DTCCIndexDownloader(_DTCCCreditDownloader):
    """Index CDS (CDX.NA.IG / CDX.NA.HY) from the CFTC SDR path — trade #30."""
    SOURCE_KEY = "dtcc_index"
