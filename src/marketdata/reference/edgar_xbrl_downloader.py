"""
SEC EDGAR XBRL fundamentals + capex downloader (stress-scenario calibration).

For each issuer and each XBRL tag, fetches the companyconcept fact series:

    /api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json

Use cases (docs/design §4.8):
  1. Hyperscaler GPU/capex spend (PaymentsToAcquirePropertyPlantAndEquipment) for
     the AI-capex bust scenario (§2.8).
  2. Bond-issuer leverage / coverage / liquidity tags to size credit-spread
     stress on bonds #15-19 and the CDS basis on #28-29.

Quarterly cadence — refresh after each earnings season. A descriptive User-Agent
is required (SEC returns 403 otherwise). Tags an issuer does not report (e.g.
Revenues vs RevenueFromContractWithCustomerExcludingAssessedTax) return 404 and
are skipped. Restated facts (same period, multiple filings) are de-duplicated to
the most recently filed value.

Output (long format): edgar_xbrl_facts.csv
  ticker, cik, tag, label, unit, start, end, val, fy, fp, form, frame, filed
"""

import logging

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.errors import MarketDataIngestError
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_FACT_FIELDS = ["start", "end", "val", "fy", "fp", "form", "frame", "filed"]


class EDGARXBRLDownloader:
    """Download EDGAR XBRL company concept facts for fundamentals + capex."""

    def __init__(self, config: dict, credentials: dict[str, str],
                 *, client: HttpClient | None = None):
        src_cfg = config["sources"]["edgar_xbrl"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        # `client` is injectable so the fetch/floor logic is testable without network.
        self._client = client if client is not None else HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["concept_template"]
        self._issuers = src_cfg["issuers"]
        self._tags = src_cfg["tags"]
        # Fail-loud floor: fewer usable facts than this signals a systemic fetch
        # failure (e.g. every request 403'd), not normal per-issuer sparsity.
        self._min_expected = int(src_cfg.get("min_expected_facts", 1))

    def run(self) -> pd.DataFrame:
        logger.info("EDGAR XBRL: %d issuers x %d tags", len(self._issuers), len(self._tags))

        frames: list[pd.DataFrame] = []
        for issuer in self._issuers:
            for tag in self._tags:
                df = self._fetch_concept(issuer, tag)
                if not df.empty:
                    frames.append(df)

        if not frames:
            raise MarketDataIngestError(
                "EDGAR XBRL: no facts retrieved for any (issuer, tag) — "
                "systemic fetch failure (User-Agent blocked / endpoint moved?)")

        combined = pd.concat(frames, ignore_index=True)
        # Restatements: keep the most recently filed value per (ticker, tag, period).
        combined = (
            combined.sort_values("filed")
            .drop_duplicates(subset=["ticker", "tag", "start", "end"], keep="last")
            .sort_values(["ticker", "tag", "end"])
        )
        # Window filter on the period end date.
        combined = combined[
            (combined["end"] >= self._start) & (combined["end"] <= self._end)
        ]

        if len(combined) < self._min_expected:
            raise MarketDataIngestError(
                f"EDGAR XBRL: {len(combined)} facts in-window < floor {self._min_expected} — "
                "refusing to overwrite; suspected systemic fetch failure")

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        # date_col="end" so re-runs upsert cleanly on the period end date + keys.
        upsert_csv(combined, out_path, date_col="end",
                   key_cols=["ticker", "tag", "start", "end"])
        logger.info("EDGAR XBRL: saved %d facts -> %s", len(combined), out_path)
        return combined

    def _fetch_concept(self, issuer: dict, tag: str) -> pd.DataFrame:
        ticker = issuer["ticker"]
        cik = issuer["cik"].lstrip("0").zfill(10)
        endpoint = self._tpl.format(cik=cik, tag=tag)

        try:
            resp = self._client.get(endpoint)
        except Exception as exc:
            # 404 = issuer doesn't report this tag; expected for some (ticker, tag) pairs.
            logger.info("EDGAR XBRL %s/%s: not available (%s)", ticker, tag, exc)
            return pd.DataFrame()

        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning("EDGAR XBRL %s/%s: JSON parse error: %s", ticker, tag, exc)
            return pd.DataFrame()

        label = payload.get("label", "")
        units = payload.get("units", {})
        rows = []
        for unit, facts in units.items():
            for f in facts:
                row = {k: f.get(k) for k in _FACT_FIELDS}
                row.update({"ticker": ticker, "cik": issuer["cik"],
                            "tag": tag, "label": label, "unit": unit})
                rows.append(row)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["val"] = pd.to_numeric(df["val"], errors="coerce")
        for c in ("start", "end", "filed"):
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["end", "val"])
        out_cols = ["ticker", "cik", "tag", "label", "unit", *_FACT_FIELDS]
        df = df[out_cols]
        logger.info("EDGAR XBRL %s/%s: %d facts", ticker, tag, len(df))
        return df
