"""
Pensford SOFR Forward Curve downloader.

What the archive Excel actually contains (confirmed by inspection of 2026-05-29 file):

  Sheet "Forward Curve" (cols 0-15):
    col 6 = settlement date (datetime, monthly steps)
    col 7 = market-expectation SOFR forward rate (decimal, e.g. 0.0362)
    col 8 = +2 standard deviations upper bound
    row 5, col 6 = the as_of date of this snapshot

  Sheet "Historical Floating Rates" (cols 0-13):
    row 7 = header row
    col 5 = date; col 10 = "30D Compounded (NY Fed) SOFR"

  NOTE: Par SOFR OIS swap rates (1Y/2Y/3Y/5Y/7Y/10Y) are shown on the Pensford
  interactive web page but are NOT in the archive Excel. Those rates are derived
  from the forward curve. For directly downloadable par swap rates, use the DTCC
  SDR IRS data (see dtcc_sdr_downloader.py).

Output files:
  pensford_sofr_forward.csv    — monthly forward SOFR path per weekly snapshot
      columns: as_of_date, settlement_date, forward_sofr, forward_sofr_upper
  pensford_historical_sofr.csv — daily 30D-compounded SOFR history from Pensford
      columns: date, sofr_30d_compounded_pensford
"""

import logging
import re
import warnings
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd

from src.common import storage

from ..common.data_store import already_downloaded_dates, get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

# Excel column indices (0-based) confirmed from actual file inspection
_FWD_DATE_COL   = 6   # settlement date in Forward Curve sheet
_FWD_MKT_COL    = 7   # market-expectation SOFR forward rate
_FWD_UPPER_COL  = 8   # +2 std-dev upper bound
_FWD_ASOF_ROW   = 5   # row containing the as_of date of the snapshot
_FWD_DATA_ROW   = 5   # first row with actual forward data

_HIST_HEADER_ROW = 7  # row containing column headers in Historical Floating Rates
_HIST_DATE_COL   = 5  # date column
_HIST_SOFR30_COL = 10 # "30D Compounded (NY Fed) SOFR"


class PensfordDownloader:
    """
    Download Pensford SOFR forward curve archive Excel files and extract:
    - Monthly projected SOFR forward path per weekly snapshot
    - Historical 30D compounded SOFR series (cross-check against FRED)
    """

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["pensford_forward_curve"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._client = HttpClient(src_cfg, credentials)
        self._base_url = src_cfg["base_url"]

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        fwd_path  = get_output_path(self._asset_class, "pensford_sofr_forward.csv")
        hist_path = get_output_path(self._asset_class, "pensford_historical_sofr.csv")

        archive_df, hist_df = self._scrape_archive(fwd_path)
        manual_df, manual_hist = self._load_manual_excel()

        # Merge forward snapshots
        fwd_frames  = [df for df in [archive_df, manual_df] if not df.empty]
        hist_frames = [df for df in [hist_df, manual_hist] if not df.empty]

        if not fwd_frames:
            logger.warning(
                "Pensford: no forward-curve data.\n"
                "  -> Manually download Excel from https://www.pensford.com/forward-curve\n"
                "     and save as %s",
                get_output_path(self._asset_class, "pensford_manual.xlsx"),
            )
        else:
            combined_fwd = pd.concat(fwd_frames, ignore_index=True)
            combined_fwd["as_of_date"] = pd.to_datetime(
                combined_fwd["as_of_date"], errors="coerce"
            ).dt.strftime("%Y-%m-%d")
            combined_fwd = combined_fwd.dropna(subset=["as_of_date"]).sort_values("as_of_date")
            # Each snapshot is a full monthly forward PATH (many settlement rows per as_of), so
            # dedup on (as_of_date, settlement_date) — NOT as_of alone, which would collapse the
            # path to one point and degenerate the bootstrapped OIS curve to ~0%.
            upsert_csv(combined_fwd, fwd_path, date_col="as_of_date",
                       key_cols=["as_of_date", "settlement_date"])
            logger.info("Pensford forward: %d rows -> %s", len(combined_fwd), fwd_path)

        if hist_frames:
            combined_hist = pd.concat(hist_frames, ignore_index=True)
            upsert_csv(combined_hist, hist_path, date_col="date")
            logger.info("Pensford historical SOFR: %d rows -> %s", len(combined_hist), hist_path)

        return (
            pd.concat(fwd_frames,  ignore_index=True) if fwd_frames  else pd.DataFrame(),
            pd.concat(hist_frames, ignore_index=True) if hist_frames else pd.DataFrame(),
        )

    # ------------------------------------------------------------------
    # Archive page scraping
    # ------------------------------------------------------------------

    def _scrape_archive(
        self, existing_fwd_path: Path | str
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        try:
            resp = self._client.get(self._cfg["archive_endpoint"])
            html = resp.text
        except Exception as exc:
            logger.warning("Pensford archive page fetch failed: %s", exc)
            return pd.DataFrame(), pd.DataFrame()

        # Find all hubfs Excel links (the archive stores files at
        # /hubfs/Forward%20Curves/Pensford%20Forward%20Curve%20-%20M.DD.YYYY.xlsx)
        links = re.findall(
            r'href=["\']([^"\']*Forward[^"\']*\.xlsx)["\']', html, re.I
        )
        if not links:
            logger.info("Pensford archive: no Excel links found in page")
            return pd.DataFrame(), pd.DataFrame()

        known_dates = already_downloaded_dates(existing_fwd_path, date_col="as_of_date")
        fwd_frames, hist_frames = [], []

        for link in links:
            full_url = link if link.startswith("http") else urljoin(self._base_url, link)
            inferred = self._infer_date_from_url(full_url)
            if inferred and inferred in known_dates:
                continue
            fwd_df, hist_df = self._download_and_parse(full_url, inferred)
            if not fwd_df.empty:
                fwd_frames.append(fwd_df)
            if not hist_df.empty:
                hist_frames.append(hist_df)

        return (
            pd.concat(fwd_frames,  ignore_index=True) if fwd_frames  else pd.DataFrame(),
            pd.concat(hist_frames, ignore_index=True) if hist_frames else pd.DataFrame(),
        )

    def _download_and_parse(
        self, url: str, fallback_date: str | None
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        try:
            parsed = urlparse(url)
            endpoint = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            resp = self._client.get(endpoint)
            wb_bytes = resp.content
        except Exception as exc:
            logger.warning("Pensford download failed %s: %s", url, exc)
            return pd.DataFrame(), pd.DataFrame()
        return self._parse_excel(wb_bytes, fallback_date or date.today().isoformat())

    # ------------------------------------------------------------------
    # Excel parsing — exact layout from file inspection
    # ------------------------------------------------------------------

    def _parse_excel(
        self, wb_bytes: bytes, fallback_date: str
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                xl = pd.ExcelFile(BytesIO(wb_bytes), engine="openpyxl")
        except Exception as exc:
            logger.warning("Pensford: Excel open failed: %s", exc)
            return pd.DataFrame(), pd.DataFrame()

        fwd_df  = self._parse_forward_curve_sheet(xl, fallback_date)
        hist_df = self._parse_historical_sofr_sheet(xl)
        return fwd_df, hist_df

    def _parse_forward_curve_sheet(
        self, xl: pd.ExcelFile, fallback_date: str
    ) -> pd.DataFrame:
        if "Forward Curve" not in xl.sheet_names:
            return pd.DataFrame()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = xl.parse("Forward Curve", header=None)
        except Exception as exc:
            logger.warning("Pensford: Forward Curve sheet parse error: %s", exc)
            return pd.DataFrame()

        # as_of date is in row 5, col 6
        try:
            as_of_raw = raw.iloc[_FWD_ASOF_ROW, _FWD_DATE_COL]
            as_of = pd.to_datetime(as_of_raw, errors="coerce")
            as_of_str = as_of.strftime("%Y-%m-%d") if pd.notna(as_of) else fallback_date
        except Exception:
            as_of_str = fallback_date

        rows = []
        for _, row in raw.iterrows():
            try:
                settle_raw = row.iloc[_FWD_DATE_COL]
                rate_raw   = row.iloc[_FWD_MKT_COL]
                upper_raw  = row.iloc[_FWD_UPPER_COL]
            except IndexError:
                continue

            settle = pd.to_datetime(settle_raw, errors="coerce")
            if pd.isna(settle):
                continue
            try:
                rate  = float(rate_raw)
                upper = float(upper_raw) if pd.notna(upper_raw) else None
            except (TypeError, ValueError):
                continue

            rows.append({
                "as_of_date":       as_of_str,
                "settlement_date":  settle.strftime("%Y-%m-%d"),
                "forward_sofr":     rate,
                "forward_sofr_upper": upper,
            })

        if not rows:
            logger.warning("Pensford: no forward-curve rows extracted (as_of=%s)", as_of_str)
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        logger.info("Pensford forward-curve: %d monthly path points (as_of=%s)", len(df), as_of_str)
        return df

    def _parse_historical_sofr_sheet(self, xl: pd.ExcelFile) -> pd.DataFrame:
        if "Historical Floating Rates" not in xl.sheet_names:
            return pd.DataFrame()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = xl.parse("Historical Floating Rates", header=None)
        except Exception as exc:
            logger.warning("Pensford: Historical Floating Rates parse error: %s", exc)
            return pd.DataFrame()

        rows = []
        for i, row in raw.iterrows():
            if i <= _HIST_HEADER_ROW:
                continue
            try:
                dt_raw   = row.iloc[_HIST_DATE_COL]
                sofr_raw = row.iloc[_HIST_SOFR30_COL]
            except IndexError:
                continue
            dt = pd.to_datetime(dt_raw, errors="coerce")
            if pd.isna(dt):
                continue
            try:
                sofr = float(sofr_raw)
            except (TypeError, ValueError):
                continue
            rows.append({"date": dt.strftime("%Y-%m-%d"), "sofr_30d_compounded_pensford": sofr})

        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).drop_duplicates("date").sort_values("date")
        logger.info("Pensford historical SOFR: %d records", len(df))
        return df

    # ------------------------------------------------------------------
    # Manual Excel fallback
    # ------------------------------------------------------------------

    def _load_manual_excel(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        p = str(get_output_path(self._asset_class, "pensford_manual.xlsx"))
        if not storage.exists(p):
            return pd.DataFrame(), pd.DataFrame()
        logger.info("Pensford: loading manual Excel: %s", p)
        return self._parse_excel(storage.read_bytes(p), date.today().isoformat())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_date_from_url(url: str) -> str | None:
        """Extract date from URL like '.../Pensford Forward Curve - 5.29.2026.xlsx'."""
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.xlsx", url, re.I)
        if m:
            month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                pass
        return None
