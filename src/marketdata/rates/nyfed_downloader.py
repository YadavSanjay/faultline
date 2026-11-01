"""
NY Fed SOFR reference-rate downloader.

Downloads six series and merges them into one wide CSV:
    date, sofr_rate, sofr_30d_avg, sofr_90d_avg, sofr_180d_avg,
    sofr_index, sofr_pct1, sofr_pct25, sofr_pct75, sofr_pct99,
    sofr_vol_bn, effr_rate

Endpoint patterns (no auth):
  Overnight SOFR + EFFR support date-range search:
    /api/rates/secured/sofr/search.json?startDate=X&endDate=Y
    /api/rates/unsecured/effr/search.json?startDate=X&endDate=Y

  Compounded averages and SOFR Index do NOT support search.json —
  they only serve last/N results. We request last/1300 (~5 years of
  business days) and filter to the configured time window.
    /api/rates/secured/sofr30day/last/1300.json
    /api/rates/secured/sofr90day/last/1300.json
    /api/rates/secured/sofr180day/last/1300.json
    /api/rates/secured/sofrai/last/1300.json
"""

import logging
from datetime import date

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

# Business-days lookback to cover 5 years safely
_LAST_N = 1300


def _year_chunks(start: str, end: str) -> list[tuple[str, str]]:
    """Split [start, end] into per-year sub-intervals."""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    chunks: list[tuple[str, str]] = []
    cur = s
    while cur <= e:
        year_end = date(cur.year, 12, 31)
        chunk_end = min(year_end, e)
        chunks.append((cur.isoformat(), chunk_end.isoformat()))
        cur = date(cur.year + 1, 1, 1)
    return chunks


class NYFedDownloader:
    """Download all NY Fed SOFR reference rates and merge into one CSV."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["nyfed_sofr"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> pd.DataFrame:
        logger.info("NYFed: fetching 6 series  window %s -> %s", self._start, self._end)

        frames: dict[str, pd.DataFrame] = {}

        # All configured NY Fed series use date-range search (search.json).
        # Compounded averages (sofr30day, sofr90day, sofr180day, sofrai) do NOT support
        # this pattern and are sourced from FRED instead (see fred_downloader.py).
        for key, spec in self._cfg["series"].items():
            capture_pct = (key == "sofr_overnight")
            df = self._fetch_by_date_range(
                key,
                spec["endpoint"],
                spec["rate_field"],
                spec["output_col"],
                capture_pct,
            )
            if not df.empty:
                frames[key] = df

        if not frames:
            logger.error("NYFed: no data retrieved for any series")
            return pd.DataFrame()

        merged = None
        for df in frames.values():
            merged = df if merged is None else merged.merge(df, on="date", how="outer")

        assert merged is not None  # frames is non-empty (guarded above)
        merged = (
            merged.sort_values("date")
            .reset_index(drop=True)
        )
        # Filter to configured window
        merged = merged[
            (merged["date"] >= self._start) & (merged["date"] <= self._end)
        ]

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(merged, out_path, date_col="date")
        logger.info("NYFed: saved %d rows -> %s", len(merged), out_path)
        return merged

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_by_date_range(
        self, key: str, endpoint: str, rate_field: str, col: str, capture_percentiles: bool
    ) -> pd.DataFrame:
        rows: list[dict] = []
        for chunk_start, chunk_end in _year_chunks(self._start, self._end):
            params = {"startDate": chunk_start, "endDate": chunk_end}
            try:
                resp = self._client.get(endpoint, params=params)
                payload = resp.json()
            except Exception as exc:
                logger.warning("NYFed %s [%s to %s]: %s", key, chunk_start, chunk_end, exc)
                continue

            for record in payload.get("refRates", []):
                row: dict = {"date": record.get("effectiveDate", "")}
                row[col] = record.get(rate_field)
                if capture_percentiles:
                    row["sofr_pct1"]   = record.get("percentile1")
                    row["sofr_pct25"]  = record.get("percentile25")
                    row["sofr_pct75"]  = record.get("percentile75")
                    row["sofr_pct99"]  = record.get("percentile99")
                    row["sofr_vol_bn"] = record.get("volumeInBillions")
                rows.append(row)

        return self._to_df(key, rows, col)

    def _fetch_last_n(
        self, key: str, endpoint: str, rate_field: str, col: str
    ) -> pd.DataFrame:
        try:
            resp = self._client.get(endpoint)
            payload = resp.json()
        except Exception as exc:
            logger.warning("NYFed %s: %s", key, exc)
            return pd.DataFrame()

        rows: list[dict] = []
        for record in payload.get("refRates", []):
            row: dict = {"date": record.get("effectiveDate", "")}
            row[col] = record.get(rate_field)
            rows.append(row)

        return self._to_df(key, rows, col)

    @staticmethod
    def _to_df(key: str, rows: list[dict], col: str) -> pd.DataFrame:
        if not rows:
            logger.warning("NYFed %s: no records", key)
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date"]).drop_duplicates("date").sort_values("date")
        df[col] = pd.to_numeric(df[col], errors="coerce")
        logger.info("NYFed %s: %d records", key, len(df))
        return df
