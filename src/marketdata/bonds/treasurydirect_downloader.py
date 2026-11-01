"""
TreasuryDirect Securities API downloader.

Downloads announced and auctioned UST security records. Primary use:
  - Identify the current on-the-run 2Y and 10Y CUSIPs (trades #20-21)
  - Maintain auction calendar for UST roll dates

Endpoints (no auth):
  GET https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json
  GET https://www.treasurydirect.gov/TA_WS/securities/announced?format=json

Both return JSON arrays of security objects. Key fields:
  cusip, type, term, termUnit, auctionDate, issueDate, maturityDate,
  interestRate, highYield, securityTerm, reopening

On-the-run identification:
  The most-recently-auctioned (not reopened) security of the target term
  is the on-the-run. Filter: type="Note", term in {"2", "10"},
  reopening=False, sort by auctionDate desc, take first.

Output files: treasurydirect_auctioned.csv, treasurydirect_announced.csv
"""

import logging

import pandas as pd

from ..common.data_store import get_output_path, save_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

# Fields to keep from the TreasuryDirect response
_KEEP_FIELDS = [
    "cusip", "type", "term", "termUnit", "interestRate", "highYield",
    "auctionDate", "issueDate", "maturityDate", "announcementDate",
    "securityTerm", "reopening", "currentlyOutstanding",
]


class TreasuryDirectDownloader:
    """Download TreasuryDirect announced + auctioned securities."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["treasurydirect"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._client = HttpClient(src_cfg, credentials)
        self._otr_terms = src_cfg.get("on_the_run_terms", ["2-Year", "10-Year"])

    def run(self) -> dict[str, pd.DataFrame]:
        results: dict[str, pd.DataFrame] = {}
        for key, endpoint in self._cfg["endpoints"].items():
            df = self._fetch(key, endpoint)
            results[key] = df
            if not df.empty:
                out_path = get_output_path(self._asset_class, self._cfg["output_files"][key])
                save_csv(df, out_path)
                logger.info("TreasuryDirect %s: saved %d rows -> %s", key, len(df), out_path)

        self._log_on_the_run(results.get("auctioned", pd.DataFrame()))
        return results

    def _fetch(self, key: str, endpoint: str) -> pd.DataFrame:
        try:
            resp = self._client.get(endpoint)
            data = resp.json()
        except Exception as exc:
            logger.warning("TreasuryDirect %s: %s", key, exc)
            return pd.DataFrame()

        if not isinstance(data, list):
            logger.warning("TreasuryDirect %s: unexpected response type %s", key, type(data))
            return pd.DataFrame()

        df = pd.DataFrame(data)
        keep = [c for c in _KEEP_FIELDS if c in df.columns]
        df = df[keep]

        for date_col in ("auctionDate", "issueDate", "maturityDate", "announcementDate"):
            if date_col in df.columns:
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")

        logger.info("TreasuryDirect %s: %d records", key, len(df))
        return df

    def _log_on_the_run(self, auctioned: pd.DataFrame) -> None:
        if auctioned.empty or "securityTerm" not in auctioned.columns:
            return
        for term in self._otr_terms:
            mask = auctioned["securityTerm"] == term
            subset = auctioned[mask]
            if "reopening" in subset.columns:
                # TreasuryDirect uses "Yes"/"No" strings (not True/False booleans)
                subset = subset[subset["reopening"].astype(str).str.upper() == "NO"]
            if subset.empty:
                logger.info("TreasuryDirect on-the-run %s: not found", term)
                continue
            if "auctionDate" in subset.columns:
                latest = subset.sort_values("auctionDate", ascending=False).iloc[0]
                logger.info(
                    "TreasuryDirect on-the-run %s: CUSIP=%s  issued=%s  matures=%s  coupon=%s%%",
                    term,
                    latest.get("cusip", "?"),
                    latest.get("issueDate", "?"),
                    latest.get("maturityDate", "?"),
                    latest.get("interestRate", "?"),
                )
