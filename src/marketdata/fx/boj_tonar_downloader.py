"""
Bank of Japan TONAR downloader — JPY OIS anchor for CIP forwards (trade #27).

BOJ Time-Series API (free, no auth, launched Feb 2026). Series:

    db=FM01  code=STRDCLUCON
    = "Call Rate, Uncollateralized Overnight, Average (Daily)", unit % p.a.

This is r_JPY in the CIP forward derivation for the 3M USD/JPY forward:
    F = S * (1 + r_USD * T) / (1 + r_JPY * T)   (r_USD = SOFR from rates pipeline)

API quirks confirmed by live inspection:
  - The startDate parameter takes YYYYMM (month) granularity, NOT YYYYMMDD,
    even though the series is daily (a YYYYMMDD value returns HTTP 400).
  - The payload is JSON: RESULTSET[0].VALUES has parallel arrays
    SURVEY_DATES (YYYYMMDD integers) and VALUES (rate or null on non-business days).

Output: boj_tonar.csv
    date, tonar_rate
"""

import logging

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class BOJTonarDownloader:
    """Download BOJ uncollateralized overnight call rate (TONAR)."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["boj_tonar"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["endpoint_template"]

    def run(self) -> pd.DataFrame:
        start_yyyymm = self._start.replace("-", "")[:6]   # "2022-01-01" -> "202201"
        endpoint = self._tpl.format(start_yyyymm=start_yyyymm)
        logger.info("BOJ TONAR: fetching from %s", start_yyyymm)

        try:
            resp = self._client.get(endpoint)
            payload = resp.json()
        except Exception as exc:
            logger.error("BOJ TONAR: request failed: %s", exc)
            return pd.DataFrame()

        if payload.get("STATUS") != 200:
            logger.error("BOJ TONAR: API status %s — %s",
                         payload.get("STATUS"), payload.get("MESSAGE"))
            return pd.DataFrame()

        result = payload.get("RESULTSET") or []
        if not result:
            logger.error("BOJ TONAR: empty RESULTSET")
            return pd.DataFrame()

        values = result[0].get("VALUES", {})
        dates = values.get("SURVEY_DATES", [])
        rates = values.get("VALUES", [])
        if not dates:
            logger.error("BOJ TONAR: no SURVEY_DATES in payload")
            return pd.DataFrame()

        df = pd.DataFrame({"date": dates, "tonar_rate": rates})
        df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d",
                                    errors="coerce").dt.strftime("%Y-%m-%d")
        df["tonar_rate"] = pd.to_numeric(df["tonar_rate"], errors="coerce")
        df = df.dropna(subset=["date", "tonar_rate"]).drop_duplicates("date").sort_values("date")
        df = df[(df["date"] >= self._start) & (df["date"] <= self._end)]

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(df, out_path, date_col="date")
        logger.info("BOJ TONAR: saved %d rows -> %s", len(df), out_path)
        return df
