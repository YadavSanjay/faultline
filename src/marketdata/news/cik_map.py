"""Ticker→CIK map for the news event stream (A0, market-intelligence P3).

The 8-K downloader (A1) keys on CIK, not ticker. A0 resolves the portfolio's 8-K filers to CIKs
from the **authoritative** SEC-published ``company_tickers.json`` and writes a small map alongside
the other news feeds. Two guards (design §2.7):

  * **Coverage floor** — fewer resolved CIKs than the config floor raises ``MarketDataIngestError``
    (a systemic failure: User-Agent blocked, endpoint moved, or the ticker list is wrong), rather
    than silently writing a thin map.
  * **Drift check** — every ticker also carried in the in-repo bond-issuer config must resolve to
    the *same* CIK. Both this map and ARag's CIK table derive from the same SEC file (§8 item 6);
    validating against SEC + the in-repo issuer CIKs is a self-contained guard that the two cannot
    silently disagree, without a fragile cross-repo read of ARag's ``entity_master`` SQL table.

Output ``cik_map.csv``: ``ticker, cik, title``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from ..common.data_store import get_output_path, save_csv
from ..common.errors import MarketDataIngestError
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _bond_issuer_ciks() -> dict[str, str]:
    """The hand-maintained bond-issuer CIKs (configs/marketdata/bonds), for the drift cross-check."""
    path = _PROJECT_ROOT / "configs" / "marketdata" / "bonds" / "bonds_marketdata.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for b in cfg.get("portfolio_bonds", {}).get("corporate", []):
        if b.get("issuer") and b.get("cik"):
            out[str(b["issuer"]).upper()] = str(b["cik"]).lstrip("0").zfill(10)
    return out


class CikMapDownloader:
    """Resolve the configured tickers to CIKs from SEC company_tickers.json (A0)."""

    def __init__(self, config: dict, credentials: dict[str, str], *, client: HttpClient | None = None):
        src_cfg = config["sources"]["sec_company_tickers"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._tickers = [str(t).upper() for t in config.get("tickers", [])]
        self._floor = int(config.get("cik_coverage_floor", max(1, len(self._tickers) - 2)))
        self._client = client if client is not None else HttpClient(src_cfg, credentials)
        self._endpoint = src_cfg["endpoint"]

    def run(self) -> pd.DataFrame:
        logger.info("CIK map: resolving %d tickers from SEC company_tickers.json", len(self._tickers))
        try:
            data = self._client.get(self._endpoint).json()
        except Exception as exc:
            raise MarketDataIngestError(f"CIK map: company_tickers.json fetch failed ({exc})") from exc

        by_ticker: dict[str, dict] = {}
        for row in data.values() if isinstance(data, dict) else []:
            t = str(row.get("ticker", "")).upper()
            if t:
                by_ticker[t] = row

        rows: list[dict[str, str]] = []
        missing: list[str] = []
        for t in self._tickers:
            row = by_ticker.get(t)
            if row is None:
                missing.append(t)
                continue
            rows.append({"ticker": t, "cik": str(row["cik_str"]).lstrip("0").zfill(10),
                         "title": str(row.get("title", ""))})

        if len(rows) < self._floor:
            raise MarketDataIngestError(
                f"CIK map: resolved {len(rows)} of {len(self._tickers)} tickers < floor {self._floor} "
                f"(missing {missing}) — refusing to write; suspected systemic fetch failure")
        if missing:
            logger.warning("CIK map: %d ticker(s) unresolved (kept above floor): %s", len(missing), missing)

        self._drift_check(rows)

        df = pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        save_csv(df, out_path)
        logger.info("CIK map: wrote %d ticker→CIK rows -> %s", len(df), out_path)
        return df

    def _drift_check(self, rows: list[dict[str, str]]) -> None:
        """Fail loud if a ticker's resolved CIK disagrees with the in-repo bond-issuer config."""
        resolved = {r["ticker"]: r["cik"] for r in rows}
        issuers = _bond_issuer_ciks()
        drift = {t: (resolved[t], issuers[t]) for t in resolved.keys() & issuers.keys()
                 if resolved[t] != issuers[t]}
        if drift:
            raise MarketDataIngestError(
                f"CIK map: drift vs bond-issuer config (ticker: sec_vs_bonds) {drift} — "
                "the two CIK sources disagree; reconcile before writing (§8 item 6)")
        checked = resolved.keys() & issuers.keys()
        logger.info("CIK map: drift check OK (%d issuer CIKs cross-verified: %s)",
                    len(checked), sorted(checked))
