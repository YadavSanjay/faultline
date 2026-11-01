"""
OpenFIGI identifier-mapping downloader — builds the canonical instruments map.

Every portfolio trade carrying a `figi_query` (equities/ETFs by ticker, corp
bonds by CUSIP) is resolved to its FIGI + standardized name/ticker/exchange via
the OpenFIGI v3 mapping API. The full 30-trade portfolio is then written to
instruments.json — the canonical cross-source identifier table (CUSIP for
TRACE/EDGAR, ticker for Yahoo/CBOE, FIGI as the canonical key).

This is one-time setup data (re-run only when a trade is added), not part of the
weekly loop. Anonymous tier: 25 requests/min, 10 mapping jobs per POST.

    POST https://api.openfigi.com/v3/mapping
    Body: [{"idType":"ID_CUSIP","idValue":"037833DT4"}, ...]

Outputs:
  instruments.json       full portfolio, each trade enriched with figi/figi_name
                         /figi_ticker/exchange where resolvable
  instruments_figi.csv   flat table of just the resolved instruments
"""

import json
import logging
from collections.abc import Iterator

import pandas as pd

from src.common import storage

from ..common.data_store import get_output_path, save_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class OpenFIGIDownloader:
    """Resolve portfolio identifiers to FIGI and emit the instruments map."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["openfigi"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._portfolio = config["portfolio"]
        self._client = HttpClient(src_cfg, credentials)
        self._endpoint = src_cfg["endpoint"]
        self._batch_size = int(src_cfg.get("batch_size", 10))

    def run(self) -> pd.DataFrame:
        # Collect the trades that carry a figi_query, preserving order.
        queryable = [t for t in self._portfolio if t.get("figi_query")]
        logger.info("OpenFIGI: %d of %d trades have a figi_query",
                    len(queryable), len(self._portfolio))

        # Map each unique (idType, idValue) once, then fan results back to trades.
        unique: dict[tuple, dict] = {}
        for t in queryable:
            q = t["figi_query"]
            unique.setdefault((q["idType"], q["idValue"]), q)

        jobs = list(unique.values())
        resolved: dict[tuple, dict] = {}
        for start in range(0, len(jobs), self._batch_size):
            batch = jobs[start:start + self._batch_size]
            for q, data in self._map_batch(batch):
                if data:
                    resolved[(q["idType"], q["idValue"])] = data

        # Enrich the full portfolio in place.
        enriched = []
        for t in self._portfolio:
            rec = dict(t)
            q = t.get("figi_query")
            if q:
                hit = resolved.get((q["idType"], q["idValue"]))
                if hit:
                    rec["figi"] = hit.get("figi")
                    rec["figi_name"] = hit.get("name")
                    rec["figi_ticker"] = hit.get("ticker")
                    rec["exchange"] = hit.get("exchCode")
                    rec["security_type"] = hit.get("securityType")
                    rec["market_sector"] = hit.get("marketSector")
                else:
                    rec["figi"] = None
                    logger.warning("OpenFIGI: no match for trade #%s (%s=%s)",
                                   t.get("trade"), q["idType"], q["idValue"])
            enriched.append(rec)

        # Write instruments.json (full portfolio map).
        json_path = get_output_path(self._asset_class, self._cfg["output_json"])
        storage.write_text_atomic(
            str(json_path),
            json.dumps(
                {"generated_from": "OpenFIGI v3 mapping", "instruments": enriched},
                indent=2,
            ),
        )
        logger.info("OpenFIGI: wrote %d instruments -> %s", len(enriched), json_path)

        # Write the flat CSV of resolved instruments.
        flat = pd.DataFrame([r for r in enriched if r.get("figi")])
        if not flat.empty:
            cols = ["trade", "display", "asset_class", "figi", "figi_ticker",
                    "figi_name", "exchange", "security_type", "market_sector",
                    "cusip", "issuer", "cik"]
            flat = flat[[c for c in cols if c in flat.columns]]
            csv_path = get_output_path(self._asset_class, self._cfg["output_csv"])
            save_csv(flat, csv_path)
            logger.info("OpenFIGI: %d resolved -> %s", len(flat), csv_path)
        else:
            logger.warning("OpenFIGI: no instruments resolved to a FIGI")

        return flat

    def _map_batch(self, batch: list[dict]) -> Iterator[tuple[dict, dict | None]]:
        """POST one batch of mapping jobs; yield (query, first_data_hit) pairs."""
        # Strip to the fields OpenFIGI expects (idType, idValue, optional exchCode).
        body = [{k: q[k] for k in ("idType", "idValue", "exchCode") if k in q}
                for q in batch]
        try:
            resp = self._client.post(self._endpoint, body=body)
            payload = resp.json()
        except Exception as exc:
            logger.warning("OpenFIGI: batch of %d failed: %s", len(batch), exc)
            return

        for q, result in zip(batch, payload, strict=False):
            data = result.get("data") if isinstance(result, dict) else None
            if data:
                yield q, data[0]
            else:
                warn = result.get("warning") if isinstance(result, dict) else result
                logger.info("OpenFIGI: no data for %s=%s (%s)",
                            q["idType"], q["idValue"], warn)
                yield q, None
