"""
FRED rate-series downloader.

Uses the fredgraph.csv endpoint (no API key, no 504 — documented in docs/design §4.3).
Downloads each series individually, then merges into one wide CSV filtered to the
configured time window.

Output columns:
    date, SOFR, SOFR30DAYAVG, SOFR90DAYAVG, SOFR180DAYAVG, SOFRINDEX,
    DGS2, DGS5, DGS10, DGS30, IORB
"""

import io
import logging

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class FREDDownloader:
    """Download FRED rate series and merge into a single wide CSV."""

    #: config key under ``sources`` this downloader reads; subclasses override for other FRED pulls
    source_key: str = "fred_rates"

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"][self.source_key]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._endpoint_tpl = src_cfg["endpoint_template"]

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> pd.DataFrame:
        series_dict: dict[str, str] = self._cfg["series"]
        logger.info("FRED: downloading %d series from %s to %s",
                    len(series_dict), self._start, self._end)

        frames: dict[str, pd.DataFrame] = {}
        for series_id in series_dict:
            df = self._fetch_series(series_id)
            if not df.empty:
                frames[series_id] = df

        if not frames:
            logger.error("FRED: no data retrieved for any series")
            return pd.DataFrame()

        merged = None
        for df in frames.values():
            merged = df if merged is None else merged.merge(df, on="date", how="outer")

        assert merged is not None  # frames is non-empty (guarded above)
        merged = (
            merged.sort_values("date")
            .reset_index(drop=True)
        )
        # Apply time-window filter
        merged = merged[
            (merged["date"] >= self._start) & (merged["date"] <= self._end)
        ]

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(merged, out_path, date_col="date")
        logger.info("FRED: saved %d rows → %s", len(merged), out_path)
        return merged

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_series(self, series_id: str) -> pd.DataFrame:
        endpoint = self._endpoint_tpl.format(series_id=series_id)
        try:
            resp = self._client.get(endpoint)
            csv_text = resp.text
        except Exception as exc:
            logger.warning("FRED %s: %s", series_id, exc)
            return pd.DataFrame()

        try:
            df = pd.read_csv(
                io.StringIO(csv_text),
                names=["date", series_id],
                skiprows=1,
            )
        except Exception as exc:
            logger.warning("FRED %s: CSV parse error: %s", series_id, exc)
            return pd.DataFrame()

        # FRED encodes missing values as "."
        df[series_id] = pd.to_numeric(df[series_id], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date"]).drop_duplicates("date").sort_values("date")
        logger.info("FRED %s: %d records", series_id, len(df))
        return df
