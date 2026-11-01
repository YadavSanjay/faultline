"""
Fed H.10 Foreign Exchange Rates downloader — USD/TWD (primary) + USD/JPY (backup).

The Federal Reserve publishes the H.10 release as one historical HTML table per
currency pair (Date, Rate), units = foreign currency per USD, updated Mondays
~16:15 ET with coverage back to 2000 in a single fetch. "ND" marks holidays.

    /releases/h10/hist/dat00_ta.htm    USD/TWD (TWD per USD)
    /releases/h10/hist/dat00_ja.htm    USD/JPY (JPY per USD) — backup to ECB

Primary source for USD/TWD spot (trade #26 — the dominant risk driver). Date
strings are DD-MMM-YY (e.g. "12-JUN-26").

Output: fed_h10_fx_rates.csv
    date, usdtwd, usdjpy
"""

import io
import logging

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class FedH10Downloader:
    """Download Fed H.10 FX history tables and merge into one wide CSV."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["fed_h10"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._series = src_cfg["series"]          # {output_col: endpoint}

    def run(self) -> pd.DataFrame:
        logger.info("Fed H.10: downloading %d FX series", len(self._series))

        merged: pd.DataFrame | None = None
        for out_col, endpoint in self._series.items():
            df = self._fetch_series(endpoint, out_col)
            if df.empty:
                continue
            merged = df if merged is None else merged.merge(df, on="date", how="outer")

        if merged is None or merged.empty:
            logger.error("Fed H.10: no data retrieved")
            return pd.DataFrame()

        merged = merged.sort_values("date")
        merged = merged[(merged["date"] >= self._start) & (merged["date"] <= self._end)]

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(merged, out_path, date_col="date")
        logger.info("Fed H.10: saved %d rows -> %s", len(merged), out_path)
        return merged

    def _fetch_series(self, endpoint: str, out_col: str) -> pd.DataFrame:
        try:
            resp = self._client.get(endpoint)
            tables = pd.read_html(io.StringIO(resp.text), flavor="lxml")
        except Exception as exc:
            logger.warning("Fed H.10 %s: %s", out_col, exc)
            return pd.DataFrame()

        if not tables:
            logger.warning("Fed H.10 %s: no tables found", out_col)
            return pd.DataFrame()

        df = tables[0]
        if df.shape[1] < 2:
            logger.warning("Fed H.10 %s: unexpected table shape %s", out_col, df.shape)
            return pd.DataFrame()

        df = df.iloc[:, :2]
        df.columns = ["date", out_col]
        # "ND" (holiday / no data) -> NaN
        df[out_col] = pd.to_numeric(df[out_col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], format="%d-%b-%y", errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date", out_col]).drop_duplicates("date")
        logger.info("Fed H.10 %s: %d records", out_col, len(df))
        return df
