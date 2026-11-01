"""
CBOE Volatility Index History downloader.

Downloads daily history CSVs for VIX and related vol indices from CBOE.

URL pattern:
  https://cdn.cboe.com/api/global/us_indices/daily_prices/{INDEX_NAME}_History.csv

Available indices (all return DATE, OPEN, HIGH, LOW, CLOSE columns):
  VIX    — S&P 500 30-day implied vol (primary market stress gauge)
  VVIX   — vol-of-vol: VIX of VIX (tail risk indicator)
  VIX9D  — 9-day implied vol (very short-dated)
  VIX3M  — 3-month implied vol
  VIX6M  — 6-month implied vol
  SKEW   — CBOE SKEW: 30-day tail risk (put skew)
  VXAPL  — Apple 30-day implied vol
  VXAZN  — Amazon 30-day implied vol
  VXGOG  — Google 30-day implied vol

Usage in our pipeline:
  - VIX: primary equity vol stress anchor for trades #7-14. VIX peak
    calibrates the vol shock vector in stress scenarios (COVID ~82, GFC ~80).
  - VIX term structure (VIX9D/VIX3M/VIX6M): shape the vol forward curve for
    longer-dated options (#7 AAPL LEAPS, #10 META 6M, #12/#13 SPX 6M).
  - SKEW: calibrates put skew for trades #10 (META put spread), #12/#13 (SPX
    puts — skew is the dominant driver of the 90-80% put spread price).
  - VXAPL: cross-check for AAPL option IVs (#7).

Output: data/marketdata/options/vol_indices/{INDEX_NAME}_History.csv
  date, open, high, low, close   (standardised lowercase column names)

Full history downloaded on first run; subsequent runs upsert only new rows.
"""

import io
import logging

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class CBOEVolIndicesDownloader:
    """Download VIX and related vol index daily history from CBOE."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["cboe_vol_indices"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._client = HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["endpoint_template"]
        self._out_dir = src_cfg["output_dir"]
        self._indices = src_cfg["indices"]
        self._start = config["time_window"]["start_date"]

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> dict[str, pd.DataFrame]:
        logger.info("CBOE vol indices: downloading %d indices", len(self._indices))
        results: dict[str, pd.DataFrame] = {}

        for index_name in self._indices:
            df = self._fetch_index(index_name)
            results[index_name] = df
            if not df.empty:
                out_path = get_output_path(
                    self._asset_class,
                    f"{self._out_dir}/{index_name}_History.csv",
                )
                upsert_csv(df, out_path, date_col="date")
                logger.info("CBOE %s: %d rows (%s -> %s) -> %s",
                            index_name, len(df),
                            df["date"].iloc[0], df["date"].iloc[-1], out_path)

        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_index(self, index_name: str) -> pd.DataFrame:
        endpoint = self._tpl.format(index_name=index_name)
        try:
            resp = self._client.get(endpoint)
            text = resp.text
        except Exception as exc:
            logger.warning("CBOE vol idx %s: %s", index_name, exc)
            return pd.DataFrame()

        # CBOE CSV header row: "DATE,OPEN,HIGH,LOW,CLOSE"
        # First line is sometimes a title row, not the header — detect
        lines = text.strip().split("\n")
        header_idx = 0
        for i, line in enumerate(lines):
            if line.strip().upper().startswith("DATE"):
                header_idx = i
                break

        try:
            df = pd.read_csv(
                io.StringIO("\n".join(lines[header_idx:])),
                na_values=["null", "N/A", ""],
            )
        except Exception as exc:
            logger.warning("CBOE vol idx %s: CSV parse error: %s", index_name, exc)
            return pd.DataFrame()

        # Normalise column names
        df.columns = [c.strip().lower() for c in df.columns]
        if "date" not in df.columns:
            logger.warning("CBOE vol idx %s: 'date' column not found", index_name)
            return pd.DataFrame()

        # Some indices (VVIX, SKEW) ship a single value column named after the
        # index (e.g. "DATE,VVIX") rather than full OHLC. Normalise that lone
        # value column to "close" so every vol-index CSV has a consistent schema.
        if "close" not in df.columns:
            value_cols = [c for c in df.columns if c != "date"]
            if len(value_cols) == 1:
                df = df.rename(columns={value_cols[0]: "close"})
            else:
                logger.warning(
                    "CBOE vol idx %s: no 'close' and %d value columns %s — leaving as-is",
                    index_name, len(value_cols), value_cols,
                )

        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

        for col in ("open", "high", "low", "close"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Filter to our time window start (keep full history if start predates VIX)
        return df[df["date"] >= self._start]
