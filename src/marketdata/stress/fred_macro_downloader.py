"""
FRED stress-macro downloader — credit-spread (OAS) + equity-vol indices.

Pulls the broad ICE BofA IG/HY option-adjusted spreads and the VIX close via the
fredgraph.csv endpoint (no API key, no /fred/series 504 — see design §4.9). These
are the stress-SPECIFIC scaling series for Methodology B: BAMLC0A0CM is the ΔOAS
scalar for the bust credit leg, BAMLH0A0HYM2 the HY risk-appetite tail, and VIXCLS
the daily equity-vol level (the authoritative intraday-high VIX archive lives in
options/vol_indices via the CBOE pipeline).

Same shape as src/marketdata/rates/fred_downloader.py but reads
sources["fred_macro"]. Output: one wide CSV, date + one column per series.
"""

import io
import logging

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class FREDMacroDownloader:
    """Download FRED OAS/VIX series and merge into a single wide CSV."""

    #: config key under ``sources`` this downloader reads; subclasses override for other FRED pulls
    source_key: str = "fred_macro"

    def __init__(self, config: dict, credentials: dict[str, str], *, client: HttpClient | None = None):
        src_cfg = config["sources"][self.source_key]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        self._client = client if client is not None else HttpClient(src_cfg, credentials)
        self._endpoint_tpl = src_cfg["endpoint_template"]

    def run(self) -> pd.DataFrame:
        series_dict: dict[str, str] = self._cfg["series"]
        logger.info("FRED-macro: downloading %d series from %s to %s",
                    len(series_dict), self._start, self._end)

        frames: dict[str, pd.DataFrame] = {}
        for series_id in series_dict:
            df = self._fetch_series(series_id)
            if not df.empty:
                frames[series_id] = df

        if not frames:
            logger.error("FRED-macro: no data retrieved for any series")
            return pd.DataFrame()

        merged = None
        for df in frames.values():
            merged = df if merged is None else merged.merge(df, on="date", how="outer")

        assert merged is not None  # frames is non-empty (guarded above)
        merged = merged.sort_values("date").reset_index(drop=True)
        merged = merged[(merged["date"] >= self._start) & (merged["date"] <= self._end)]

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(merged, out_path, date_col="date")
        logger.info("FRED-macro: saved %d rows -> %s", len(merged), out_path)
        return merged

    def _fetch_series(self, series_id: str) -> pd.DataFrame:
        endpoint = self._endpoint_tpl.format(series_id=series_id)
        try:
            resp = self._client.get(endpoint)
            csv_text = resp.text
        except Exception as exc:
            logger.warning("FRED-macro %s: %s", series_id, exc)
            return pd.DataFrame()

        try:
            df = pd.read_csv(io.StringIO(csv_text), names=["date", series_id], skiprows=1)
        except Exception as exc:
            logger.warning("FRED-macro %s: CSV parse error: %s", series_id, exc)
            return pd.DataFrame()

        # FRED encodes missing values as "."
        df[series_id] = pd.to_numeric(df[series_id], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date"]).drop_duplicates("date").sort_values("date")
        logger.info("FRED-macro %s: %d records", series_id, len(df))
        return df


class FREDThroughputDownloader(FREDMacroDownloader):
    """FRED semiconductor-industrial-production series into ``stress/fred_throughput.csv`` (F9).

    IPG3344S (semiconductor & related-device manufacturing IP) is a physical corroboration of the
    AI-capex cycle. Kept in a separate file from ``fred_stress_macro.csv`` so it stays out of the
    credit factor's file. Reuses the macro fetch/merge; only the ``sources`` key differs.
    """

    source_key = "fred_throughput"
