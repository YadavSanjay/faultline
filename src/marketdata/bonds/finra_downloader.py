"""
FINRA Fixed Income Market Data API downloader.

Confirmed behaviour (from live API testing):
- OAuth2 client-credentials token: POST to /oauth2/access_token (Basic auth header)
- Data endpoints: POST with dateRangeFilters body → returns CSV (not JSON)
- Only two confirmed-working datasets for our scope:
    treasuryDailyAggregates  — UST daily trading volume/price by security type
    treasuryMonthlyAggregates — UST monthly aggregates
- Corporate bond datasets (corporateDebtMarketBreadth etc.) return 404 — not available
  with standard API credentials; they appear to require additional entitlement

CSV columns for treasuryDailyAggregates:
    tradeDate, productCategory, yearsToMaturity, benchmark,
    atsInterdealerCount, atsInterdealerVolume,
    dealerCustomerCount, dealerCustomerVolume,
    volumeWeightedAveragePrice

Use case: macro stress context for UST market liquidity (trades #20-21).
"""

import base64
import io
import logging
import time
from typing import cast

import pandas as pd
import requests

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import user_agent

logger = logging.getLogger(__name__)

_PAGE_LIMIT = 5000


class FINRADownloader:
    """Download FINRA UST market aggregates via OAuth2 API → CSV response."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["finra"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end   = config["time_window"]["end_date"]
        self._creds = credentials

        auth_cfg = src_cfg["auth"]
        self._token_url     = auth_cfg["token_url"]
        self._client_id     = credentials.get(auth_cfg["client_id_env"], "")
        self._client_secret = credentials.get(auth_cfg["client_secret_env"], "")
        self._token: str    = ""
        self._token_expires: float = 0.0

        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent(credentials)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> dict[str, pd.DataFrame]:
        self._ensure_token()

        # Only confirmed-working datasets (corp datasets return 404)
        active_datasets = {
            k: v for k, v in self._cfg["datasets"].items()
            if k in ("treasury_daily", "treasury_monthly")
        }
        if not active_datasets:
            logger.warning("FINRA: no active datasets configured")
            return {}

        results: dict[str, pd.DataFrame] = {}
        for key, endpoint in active_datasets.items():
            logger.info("FINRA %s: querying %s", key, endpoint)
            df = self._query_csv(endpoint)
            results[key] = df
            if not df.empty:
                out_path = get_output_path(self._asset_class, self._cfg["output_files"][key])
                upsert_csv(df, out_path, date_col="date")
                logger.info("FINRA %s: saved %d rows -> %s", key, len(df), out_path)
            else:
                logger.warning("FINRA %s: no data returned", key)

        return results

    # ------------------------------------------------------------------
    # OAuth2
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        if self._token and time.monotonic() < self._token_expires - 60:
            return
        self._token = self._fetch_token()
        self._token_expires = time.monotonic() + 1800
        self._session.headers["Authorization"] = f"Bearer {self._token}"

    def _fetch_token(self) -> str:
        if not self._client_id or not self._client_secret:
            raise RuntimeError("FINRA OAuth2: set FINRA_API_KEY and FINRA_API_KEY_SECRET in .env")
        cred = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        resp = self._session.post(
            self._token_url,
            params={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {cred}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token", "")
        if not token:
            raise RuntimeError(f"No access_token in FINRA response: {resp.text[:200]}")
        logger.info("FINRA OAuth2: token obtained")
        return str(token)

    # ------------------------------------------------------------------
    # Data query — POST with dateRangeFilters, response is CSV
    # ------------------------------------------------------------------

    def _query_csv(self, endpoint: str) -> pd.DataFrame:
        url = self._cfg["base_url"].rstrip("/") + endpoint
        body = {
            "limit": _PAGE_LIMIT,
            "offset": 0,
            "dateRangeFilters": [
                {"startDate": self._start, "endDate": self._end,
                 "fieldName": self._cfg.get("date_field", "tradeDate")},
            ],
        }

        all_frames: list[pd.DataFrame] = []
        while True:
            self._ensure_token()
            try:
                resp = self._session.post(url, json=body,
                                          timeout=self._cfg.get("timeout_s", 30))
                if resp.status_code == 401:
                    self._token_expires = 0
                    self._ensure_token()
                    resp = self._session.post(url, json=body,
                                              timeout=self._cfg.get("timeout_s", 30))
                if not resp.ok:
                    logger.warning("FINRA %s: HTTP %s  %s", endpoint, resp.status_code,
                                   resp.text[:200])
                    break
            except Exception as exc:
                logger.warning("FINRA %s: %s", endpoint, exc)
                break

            # Response is CSV, not JSON
            try:
                df = pd.read_csv(io.StringIO(resp.text))
            except Exception as exc:
                logger.warning("FINRA %s: CSV parse error: %s  raw=%s",
                               endpoint, exc, resp.text[:100])
                break

            if df.empty:
                break

            all_frames.append(df)
            if len(df) < _PAGE_LIMIT:
                break
            body["offset"] = cast("int", body["offset"]) + _PAGE_LIMIT
            time.sleep(self._cfg.get("rate_limit_delay_s", 0.5))

        if not all_frames:
            return pd.DataFrame()

        combined = pd.concat(all_frames, ignore_index=True)
        date_field = self._cfg.get("date_field", "tradeDate")
        if date_field in combined.columns:
            combined = combined.rename(columns={date_field: "date"})
            combined["date"] = pd.to_datetime(combined["date"],
                                               errors="coerce").dt.strftime("%Y-%m-%d")
            combined = combined.dropna(subset=["date"]).sort_values("date")
        return combined
