"""
Treasury.gov Daily Par Yield Curve downloader.

Fetches the full 14-tenor UST par yield curve (1M, 2M, 3M, 4M, 6M, 1Y, 2Y, 3Y,
5Y, 7Y, 10Y, 20Y, 30Y) published by the US Treasury at ~3 pm ET each business day.

URL pattern (one CSV per calendar month):
  https://home.treasury.gov/resource-center/data-chart-center/interest-rates/
      daily-treasury-rates.csv/all/{YYYYMM}?type=daily_treasury_yield_curve
      &field_tdr_date_value_month={YYYYMM}&page&_format=csv

Strategy: iterate over each month in [start_date, end_date], fetch the CSV,
normalise column names, and upsert into a single output file.

Output: treasury_par_yields.csv
  date, t1mo, t2mo, t3mo, t4mo, t6mo, t1yr, t2yr, t3yr, t5yr, t7yr,
  t10yr, t20yr, t30yr
"""

import io
import logging
from datetime import date

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_RAW_TO_OUTPUT = {
    "Date":   "date",
    "1 Mo":   "t1mo",   "2 Mo":   "t2mo",  "3 Mo":  "t3mo",
    "4 Mo":   "t4mo",   "6 Mo":   "t6mo",  "1 Yr":  "t1yr",
    "2 Yr":   "t2yr",   "3 Yr":   "t3yr",  "5 Yr":  "t5yr",
    "7 Yr":   "t7yr",   "10 Yr":  "t10yr", "20 Yr": "t20yr",
    "30 Yr":  "t30yr",
}


def _month_range(start: str, end: str) -> list[str]:
    """Return list of 'YYYYMM' strings covering [start, end]."""
    s = date.fromisoformat(start).replace(day=1)
    e = date.fromisoformat(end).replace(day=1)
    months = []
    cur = s
    while cur <= e:
        months.append(cur.strftime("%Y%m"))
        m = cur.month + 1
        cur = cur.replace(year=cur.year + (m - 1) // 12, month=((m - 1) % 12) + 1)
    return months


class TreasuryGovDownloader:
    """Download Treasury.gov daily par yield curves month by month."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["treasury_gov"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end   = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["endpoint_template"]

    def run(self) -> pd.DataFrame:
        months = _month_range(self._start, self._end)
        logger.info("Treasury.gov: fetching %d months of par yield curves", len(months))

        frames = []
        for yyyymm in months:
            df = self._fetch_month(yyyymm)
            if not df.empty:
                frames.append(df)

        if not frames:
            logger.error("Treasury.gov: no data retrieved")
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        combined = combined.sort_values("date").drop_duplicates("date")

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(combined, out_path, date_col="date")
        logger.info("Treasury.gov: saved %d rows -> %s", len(combined), out_path)
        return combined

    def _fetch_month(self, yyyymm: str) -> pd.DataFrame:
        endpoint = self._tpl.replace("{yyyymm}", yyyymm)
        try:
            resp = self._client.get(endpoint)
            text = resp.text
        except Exception as exc:
            logger.warning("Treasury.gov %s: %s", yyyymm, exc)
            return pd.DataFrame()

        try:
            df = pd.read_csv(io.StringIO(text))
        except Exception as exc:
            logger.warning("Treasury.gov %s: CSV parse error: %s", yyyymm, exc)
            return pd.DataFrame()

        df = df.rename(columns={c: _RAW_TO_OUTPUT.get(c, c) for c in df.columns})
        # Keep only our expected columns
        keep = list(_RAW_TO_OUTPUT.values())
        df = df[[c for c in keep if c in df.columns]]
        for col in df.columns:
            if col != "date":
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date"])
        logger.debug("Treasury.gov %s: %d rows", yyyymm, len(df))
        return df
