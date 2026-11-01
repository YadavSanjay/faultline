"""
Stooq Daily OHLCV downloader for Cash Equity trades #1-6.

DISABLED (2026-07): Stooq's CSV endpoint now returns JS-rendered HTML for automated requests
(even with the API key), so it is broken as an automated feed. The Yahoo chart API
(`yahoo_downloader.py`) is the primary OHLCV source; this downloader is retained as a fallback
and is `enabled: false` in the config. Historically Stooq offered:
  - No authentication or API key
  - No practical rate limits
  - Clean CSV response
  - Date range filtering via d1/d2 params (YYYYMMDD)

URL pattern:
  https://stooq.com/q/d/l/?s={ticker}&i=d&d1={YYYYMMDD}&d2={YYYYMMDD}
  i=d → daily bars; i=w → weekly bars

Ticker conventions:
  US stocks/ADRs: nvda.us, tsm.us, avgo.us, mu.us
  US ETFs:        xlk.us, smh.us
  Taiwan listing: 2330.tw (for cross-check on TSM ADR pricing)

Output: prices_daily.csv  (long format, all tickers stacked)
  date, ticker, open, high, low, close, volume

Incremental: only fetches dates not already in the output file.
"""

import io
import logging
from datetime import timedelta

import pandas as pd

from ..common.data_store import get_output_path, load_csv, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_STOOQ_COLS = {
    "Date":   "date",
    "Open":   "open",
    "High":   "high",
    "Low":    "low",
    "Close":  "close",
    "Volume": "volume",
}


class StooqDownloader:
    """Download daily OHLCV from Stooq for all portfolio equity instruments."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["stooq"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._portfolio = config["portfolio"]
        self._start = config["time_window"]["start_date"]
        self._end   = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["endpoint_template"]
        self._out_path = get_output_path(self._asset_class, src_cfg["output_file"])

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> pd.DataFrame:
        logger.info("Stooq: downloading daily OHLCV for %d instruments", len(self._portfolio))

        existing = load_csv(self._out_path)
        frames = []

        for inst in self._portfolio:
            ticker    = inst["ticker"]
            stooq_sym = inst["stooq"]

            # Determine incremental start: day after last existing date for this ticker
            fetch_start = self._incremental_start(existing, ticker)
            if fetch_start > self._end:
                logger.info("Stooq %s: already up-to-date", ticker)
                continue

            df = self._fetch_ticker(ticker, stooq_sym, fetch_start, self._end)
            if not df.empty:
                frames.append(df)

        if not frames:
            logger.info("Stooq: no new data to write")
            return existing if not existing.empty else pd.DataFrame()

        new_data = pd.concat(frames, ignore_index=True)
        upsert_csv(new_data, self._out_path, date_col="date", key_cols=["date", "ticker"])
        logger.info("Stooq: wrote %d new rows -> %s", len(new_data), self._out_path)
        return new_data

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _incremental_start(self, existing: pd.DataFrame, ticker: str) -> str:
        """Return the first date to fetch (day after the last date we have)."""
        if existing.empty or "ticker" not in existing.columns or "date" not in existing.columns:
            return str(self._start)
        ticker_rows = existing[existing["ticker"] == ticker]
        if ticker_rows.empty:
            return str(self._start)
        last_date = pd.to_datetime(ticker_rows["date"]).max()
        return str((last_date + timedelta(days=1)).strftime("%Y-%m-%d"))

    def _fetch_ticker(self, ticker: str, stooq_sym: str,
                      start: str, end: str) -> pd.DataFrame:
        d1 = start.replace("-", "")
        d2 = end.replace("-", "")
        endpoint = self._tpl.format(
            stooq_ticker=stooq_sym,
            start_yyyymmdd=d1,
            end_yyyymmdd=d2,
        )

        try:
            resp = self._client.get(endpoint)
            text = resp.text
        except Exception as exc:
            logger.warning("Stooq %s: %s", ticker, exc)
            return pd.DataFrame()

        # Stooq returns an HTML error page (not CSV) for unknown tickers or
        # future dates — detect by checking the first non-empty line
        first_line = text.strip().split("\n")[0] if text.strip() else ""
        if not first_line.startswith("Date"):
            logger.warning("Stooq %s: unexpected response (not CSV): %s",
                           ticker, first_line[:80])
            return pd.DataFrame()

        try:
            df = pd.read_csv(io.StringIO(text))
        except Exception as exc:
            logger.warning("Stooq %s: CSV parse error: %s", ticker, exc)
            return pd.DataFrame()

        df = df.rename(columns={c: _STOOQ_COLS.get(c, c) for c in df.columns})
        df = df[[c for c in _STOOQ_COLS.values() if c in df.columns]].copy()

        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date"])

        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Stooq returns data in reverse-chron order; sort ascending
        df = df.sort_values("date").reset_index(drop=True)
        df.insert(1, "ticker", ticker)

        logger.info("Stooq %s: %d rows (%s -> %s)",
                    ticker, len(df), df["date"].iloc[0], df["date"].iloc[-1])
        return df

    @staticmethod
    def _upsert_key(df: pd.DataFrame) -> pd.Series:
        return df["date"].astype(str) + "|" + df["ticker"].astype(str)
