"""
SEC EDGAR Bond Reference Data downloader.

For each of the 5 corporate bond issuers (trades #15-19), fetches the EDGAR
submissions JSON and extracts the 424B5 prospectus filing index.

Purpose: static reference — confirms our hardcoded bond CUSIPs are correct,
provides accession numbers to fetch the actual prospectus if needed, and
supplies bond term dates directly from SEC filings. Refetch quarterly.

Endpoint: https://data.sec.gov/submissions/CIK{cik}.json
Auth: User-Agent header required (SEC blocks automated requests without it).
Rate limit: 10 req/s — enforce 0.15s delay between calls.

Output: edgar_bond_filings.csv
  issuer, cik, accession_number, filing_date, form_type, primary_document,
  filing_url
"""

import logging

import pandas as pd

from ..common.data_store import get_output_path, save_csv
from ..common.errors import MarketDataIngestError
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_EDGAR_VIEWER = "https://www.sec.gov/Archives/edgar/data"


class EDGARDownloader:
    """Fetch 424B5 filing index for each corp bond issuer from SEC EDGAR."""

    def __init__(self, config: dict, credentials: dict[str, str],
                 *, client: HttpClient | None = None):
        src_cfg = config["sources"]["edgar"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._bonds = config["portfolio_bonds"]["corporate"]
        # `client` is injectable so the fetch/floor logic is testable without network.
        self._client = client if client is not None else HttpClient(src_cfg, credentials)
        raw_filter = src_cfg.get("form_type_filter", ["424B5", "424B2"])
        self._form_filter: set[str] = (
            set(raw_filter) if isinstance(raw_filter, list) else {raw_filter}
        )
        self._tpl = src_cfg["endpoint_template"]
        # Fail-loud floor: fewer filing records than this signals a systemic fetch
        # failure (e.g. User-Agent blocked across all issuers), not normal sparsity.
        self._min_expected = int(src_cfg.get("min_expected_filings", 1))

    def run(self) -> pd.DataFrame:
        logger.info("EDGAR: fetching filing index for %d issuers", len(self._bonds))
        frames = []
        for bond in self._bonds:
            df = self._fetch_issuer(bond)
            if not df.empty:
                frames.append(df)

        if not frames:
            raise MarketDataIngestError(
                "EDGAR: no filing data retrieved for any issuer — "
                "systemic fetch failure (User-Agent blocked / endpoint moved?)")

        combined = pd.concat(frames, ignore_index=True)
        if len(combined) < self._min_expected:
            raise MarketDataIngestError(
                f"EDGAR: {len(combined)} filing records < floor {self._min_expected} — "
                "refusing to overwrite; suspected systemic fetch failure")

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        save_csv(combined, out_path)
        logger.info("EDGAR: saved %d filing records -> %s", len(combined), out_path)
        return combined

    def _fetch_issuer(self, bond: dict) -> pd.DataFrame:
        issuer = bond["issuer"]
        cik_raw = bond["cik"]
        cik_padded = cik_raw.lstrip("0").zfill(10)
        endpoint = self._tpl.replace("{cik}", cik_padded)

        try:
            resp = self._client.get(endpoint)
            data = resp.json()
        except Exception as exc:
            logger.warning("EDGAR %s (CIK %s): %s", issuer, cik_padded, exc)
            return pd.DataFrame()

        filings = data.get("filings", {}).get("recent", {})
        if not filings:
            logger.warning("EDGAR %s: no recent filings returned", issuer)
            return pd.DataFrame()

        # Build a DataFrame from the parallel arrays in the recent filings dict
        try:
            df = pd.DataFrame({
                "form_type":         filings.get("form", []),
                "filing_date":       filings.get("filingDate", []),
                "accession_number":  filings.get("accessionNumber", []),
                "primary_document":  filings.get("primaryDocument", []),
            })
        except Exception as exc:
            logger.warning("EDGAR %s: response parse error: %s", issuer, exc)
            return pd.DataFrame()

        # Filter to our target form types (424B5 for Oracle/Amazon, 424B2 for Apple/MSFT/Meta)
        df = df[df["form_type"].isin(self._form_filter)].copy()
        if df.empty:
            logger.info("EDGAR %s: no %s filings found", issuer, self._form_filter)
            return pd.DataFrame()

        df["issuer"] = issuer
        df["cik"] = cik_raw

        # Build a human-readable viewer URL
        cik_num = cik_raw.lstrip("0")
        df["filing_url"] = df["accession_number"].apply(
            lambda acc: f"{_EDGAR_VIEWER}/{cik_num}/{acc.replace('-', '')}/{acc.replace('-', '')}-index.htm"
        )

        df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.sort_values("filing_date", ascending=False)
        logger.info("EDGAR %s: %d filings found across %s (most recent: %s)",
                    issuer, len(df), sorted(self._form_filter), df["filing_date"].iloc[0])
        return df[["issuer", "cik", "form_type", "filing_date",
                   "accession_number", "primary_document", "filing_url"]]
