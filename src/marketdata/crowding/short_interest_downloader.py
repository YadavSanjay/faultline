"""FINRA consolidated short-interest downloader (F5, market-intelligence P7).

Every name in this book is consensus-long; the dominant non-fundamental risk is crowding — everyone
owning the same AI names and leaving together (FINDING 5). This ingests FINRA's twice-monthly
consolidated short interest per portfolio name: current short position, average daily volume, and
**days-to-cover** (short position / ADV) — the direct crowding + squeeze-risk metric. Free JSON/CSV
API, no key (a POST query per symbol with a settlement-date floor).

The #6 SMH position is a **short**, so a crowded SMH short is *squeeze* risk — an upside tail a
long-book stress framework will never flag; scenariogen surfaces that asymmetry.

Output ``crowding/short_interest.csv``: ``settlement_date, symbol, short_position, adv,
days_to_cover, change_pct``. Fail-loud below a floor (a systemic fetch failure, not sparsity).
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from ..common.data_store import get_output_path, save_csv
from ..common.errors import MarketDataIngestError
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_FIELDS = {
    "settlementDate": "settlement_date",
    "symbolCode": "symbol",
    "currentShortPositionQuantity": "short_position",
    "averageDailyVolumeQuantity": "adv",
    "daysToCoverQuantity": "days_to_cover",
    "changePercent": "change_pct",
}


class ShortInterestDownloader:
    """Fetch FINRA consolidated short interest per portfolio name (F5)."""

    def __init__(self, config: dict, credentials: dict[str, str], *, client: HttpClient | None = None):
        src_cfg = config["sources"]["finra_short_interest"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._tickers = [str(t).upper() for t in config.get("tickers", [])]
        self._start = config["time_window"]["start_date"]
        self._client = client if client is not None else HttpClient(src_cfg, credentials)
        self._endpoint = src_cfg["endpoint"]
        self._limit = int(src_cfg.get("page_limit", 300))
        self._min_expected = int(src_cfg.get("min_expected_rows", 1))

    def run(self) -> pd.DataFrame:
        logger.info("FINRA short interest: fetching %d name(s)", len(self._tickers))
        frames = [df for df in (self._fetch(t) for t in self._tickers) if not df.empty]
        if not frames:
            raise MarketDataIngestError(
                "FINRA short interest: no data for any name — systemic fetch failure "
                "(endpoint moved / query rejected?)")
        combined = pd.concat(frames, ignore_index=True).sort_values(
            ["symbol", "settlement_date"]).reset_index(drop=True)
        if len(combined) < self._min_expected:
            raise MarketDataIngestError(
                f"FINRA short interest: {len(combined)} rows < floor {self._min_expected} — "
                "refusing to overwrite; suspected systemic fetch failure")
        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        save_csv(combined, out_path)
        logger.info("FINRA short interest: saved %d rows across %d name(s) -> %s",
                    len(combined), combined["symbol"].nunique(), out_path)
        return combined

    def _fetch(self, ticker: str) -> pd.DataFrame:
        body = {
            "limit": self._limit,
            "compareFilters": [
                {"fieldName": "symbolCode", "fieldValue": ticker, "compareType": "equal"},
                {"fieldName": "settlementDate", "fieldValue": self._start, "compareType": "gte"},
            ],
        }
        try:
            text = self._client.post(self._endpoint, body=body).text
            df = pd.read_csv(io.StringIO(text))
        except Exception as exc:
            logger.warning("FINRA short interest %s: %s", ticker, exc)
            return pd.DataFrame()
        if df.empty or "symbolCode" not in df.columns:
            return pd.DataFrame()
        keep = [c for c in _FIELDS if c in df.columns]
        out = df[keep].rename(columns=_FIELDS)
        for col in ("short_position", "adv", "days_to_cover", "change_pct"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        out["settlement_date"] = out["settlement_date"].astype(str)
        out["symbol"] = ticker
        out = out.dropna(subset=["days_to_cover"]).drop_duplicates("settlement_date")
        logger.info("FINRA short interest %s: %d record(s)", ticker, len(out))
        return out
