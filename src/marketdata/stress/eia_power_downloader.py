"""
EIA datacenter-grid electricity-demand downloader.

Methodology B step 2 (§4.9): pull daily electricity demand (type=D) for the
datacenter-heavy balancing authorities — PJM (N. Virginia "Data Center Alley"),
ERCO (Texas), MISO, CISO — to independently corroborate or contradict hyperscaler
capex guidance. Rising regional baseload that leads capex confirms the buildout is
real; flattening load is an early bust signal, and grid interconnection limits are
the binding physical constraint on the boom.

EIA v2 API: a FREE key (instant signup at https://www.eia.gov/opendata/) is
required IN the query string. If EIA_API_KEY is unset the source logs a warning
and is skipped (no crash), mirroring the optional-key handling elsewhere.

The daily-region-data series buckets the hourly data into calendar days *per
timezone*, so a naive query returns ~5 near-identical rows (~0.1% apart) per
(date, respondent) — one for each tz boundary (Pacific/Central/Mountain/Eastern/
Arizona; there is no UTC option). We therefore query each respondent in ITS LOCAL
timezone (config respondent_timezones), giving the true local-calendar-day demand
and exactly one row per (date, respondent).

The API returns JSON paginated in pages of up to 5000 rows:
  response.total           — total matching rows (string)
  response.data[]          — {period, respondent, respondent-name, type, timezone, value, value-units}

Output: eia_power_demand.csv — date, respondent, demand_mwh  (long format)
"""

import logging

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class EIAPowerDownloader:
    """Fetch daily electricity demand for datacenter-heavy grids from EIA v2."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["eia_power"]
        self._cfg = src_cfg
        self._creds = credentials
        self._asset_class = config["asset_class"]
        self._start = src_cfg.get("start_date_override", config["time_window"]["start_date"])
        self._end = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._endpoint = src_cfg["endpoint_path"]
        self._key_env = src_cfg.get("auth", {}).get("key_env", "EIA_API_KEY")

    def run(self) -> pd.DataFrame:
        if not self._creds.get(self._key_env):
            logger.warning(
                "EIA: %s not set - skipping power-demand pull. Get a free key at "
                "https://www.eia.gov/opendata/ and add it to .env.", self._key_env)
            return pd.DataFrame()

        resp_tz: dict[str, str] = self._cfg["respondent_timezones"]
        logger.info("EIA: downloading daily demand for %s from %s to %s",
                    list(resp_tz), self._start, self._end)

        all_rows: list[dict] = []
        for respondent, tz in resp_tz.items():
            rows = self._fetch_paginated(respondent, tz)
            all_rows.extend(rows)

        if not all_rows:
            logger.warning("EIA: no rows returned")
            return pd.DataFrame()

        df = pd.DataFrame(all_rows)
        df = df.rename(columns={"period": "date", "value": "demand_mwh"})
        df["demand_mwh"] = pd.to_numeric(df["demand_mwh"], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = (
            df.dropna(subset=["date", "demand_mwh"])
            [["date", "respondent", "demand_mwh"]]
            # defensive: one row per (date, respondent) even if EIA returns extras
            .drop_duplicates(["date", "respondent"], keep="last")
            .sort_values(["respondent", "date"])
            .reset_index(drop=True)
        )

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(df, out_path, date_col="date", key_cols=["date", "respondent"])
        logger.info("EIA: saved %d rows -> %s", len(df), out_path)
        return df

    def _fetch_paginated(self, respondent: str, timezone: str) -> list[dict]:
        length = int(self._cfg.get("page_length", 5000))
        base_params = {
            "frequency": self._cfg["frequency"],
            "data[0]": "value",
            "facets[type][]": self._cfg["demand_type"],
            "facets[respondent][]": respondent,
            "facets[timezone][]": timezone,
            "start": self._start,
            "end": self._end,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": length,
        }

        all_rows: list[dict] = []
        offset = 0
        while True:
            params = dict(base_params, offset=offset)
            try:
                resp = self._client.get(self._endpoint, params=params)
                payload = resp.json().get("response", {})
            except Exception as exc:
                logger.warning("EIA: page at offset %d failed: %s", offset, exc)
                break

            data = payload.get("data", [])
            if not data:
                break
            all_rows.extend(data)

            total = int(payload.get("total", len(all_rows)))
            logger.info("EIA %s (%s): fetched %d/%d rows", respondent, timezone, len(all_rows), total)
            offset += length
            if offset >= total:
                break

        return all_rows
