"""
ECB daily reference-rate downloader — USD/JPY cross (trade #27).

The ECB publishes EUR-base reference rates daily. USD/JPY (and any USD cross) is
derived from two EUR legs:

    USD/JPY = (JPY/EUR) / (USD/EUR)

Both legs come from the EXR dataflow as CSV (no auth):

    /service/data/EXR/D.USD.EUR.SP00.A?format=csvdata
    /service/data/EXR/D.JPY.EUR.SP00.A?format=csvdata

Each response carries TIME_PERIOD (ISO date) and OBS_VALUE columns. ECB does NOT
publish TWD — USD/TWD comes from fed_h10 / cbc_twd_spot.

Output: ecb_fx_rates.csv
    date, usd_eur, jpy_eur, usdjpy
"""

import io
import logging

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class ECBFxDownloader:
    """Download ECB EUR-base reference rates and derive the USD/JPY cross."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["ecb_fx"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["endpoint_template"]
        self._series = src_cfg["series"]          # {output_col: ccy}
        self._cross = src_cfg.get("cross")        # {name, formula}

    def run(self) -> pd.DataFrame:
        logger.info("ECB: downloading %d reference-rate series", len(self._series))

        merged: pd.DataFrame | None = None
        for out_col, ccy in self._series.items():
            df = self._fetch_series(ccy, out_col)
            if df.empty:
                continue
            merged = df if merged is None else merged.merge(df, on="date", how="outer")

        if merged is None or merged.empty:
            logger.error("ECB: no data retrieved")
            return pd.DataFrame()

        # Derive the cross (e.g. usdjpy = jpy_eur / usd_eur).
        if self._cross:
            name, formula = self._cross["name"], self._cross["formula"]
            try:
                merged[name] = merged.eval(formula)
            except Exception as exc:
                logger.warning("ECB: cross %r eval failed (%s)", name, exc)

        merged = merged.sort_values("date")
        merged = merged[(merged["date"] >= self._start) & (merged["date"] <= self._end)]

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(merged, out_path, date_col="date")
        logger.info("ECB: saved %d rows -> %s", len(merged), out_path)
        return merged

    def _fetch_series(self, ccy: str, out_col: str) -> pd.DataFrame:
        endpoint = self._tpl.format(ccy=ccy)
        try:
            resp = self._client.get(endpoint)
            df = pd.read_csv(io.StringIO(resp.text))
        except Exception as exc:
            logger.warning("ECB %s/EUR: %s", ccy, exc)
            return pd.DataFrame()

        if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
            logger.warning("ECB %s/EUR: unexpected columns %s", ccy, list(df.columns))
            return pd.DataFrame()

        out = df[["TIME_PERIOD", "OBS_VALUE"]].rename(
            columns={"TIME_PERIOD": "date", "OBS_VALUE": out_col}
        )
        out[out_col] = pd.to_numeric(out[out_col], errors="coerce")
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        out = out.dropna(subset=["date", out_col]).drop_duplicates("date")
        logger.info("ECB %s/EUR: %d records", ccy, len(out))
        return out
