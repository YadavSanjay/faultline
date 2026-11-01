"""SEC EDGAR 8-K item-code event stream (A1, market-intelligence P3).

For each portfolio 8-K filer (resolved to a CIK by A0), fetches the EDGAR submissions JSON and
extracts every Form 8-K whose item codes intersect the allowlist (design §2.2). An 8-K is filed
*only* when something material happens and the filer tags *which* material thing from a fixed
numbered taxonomy under legal liability — so the classification is done, citable by accession, and
needs no LLM. Volume is bounded by construction (~8 8-Ks per name per ~5 months).

Structured metadata only — no NLP, no document text. Output ``sec_events.csv``:
``ticker, cik, filing_date, form, item_codes, accession, doc_url, weight``, where ``weight`` is the
summed allowlist weight of the filing's relevant items (the raw signal the scenariogen mapping then
decays by age and clamps into a multiplier).

Fail-loud below a floor: too few events across all names signals a systemic fetch failure
(User-Agent blocked / endpoint moved), not normal sparsity — raises ``MarketDataIngestError``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence

import pandas as pd

from ..common.data_store import get_output_path, load_csv, save_csv
from ..common.errors import MarketDataIngestError
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_EDGAR_VIEWER = "https://www.sec.gov/Archives/edgar/data"


class SecEventsDownloader:
    """Fetch allowlisted 8-K events per CIK from SEC EDGAR (A1)."""

    def __init__(self, config: dict, credentials: dict[str, str], *, client: HttpClient | None = None):
        src_cfg = config["sources"]["sec_events"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._allow: dict[str, float] = {str(k): float(v) for k, v in config.get("item_allowlist", {}).items()}
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        self._cik_map_file = str(config.get("cik_map_file", "cik_map.csv"))
        self._client = client if client is not None else HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["endpoint_template"]
        self._min_expected = int(src_cfg.get("min_expected_events", 1))
        # Foreign private issuers (TSM) file 6-Ks with no item codes; ordered file-name rules map them to an
        # 8-K-equivalent item. Checked up front: a rule naming an item outside the allowlist fails here.
        self._six_k_rules: list[dict[str, str | None]] = list(config.get("six_k_rules", []))
        six_k_rows({}, self._six_k_rules, self._allow, ticker="", cik="", start=self._start, end=self._end)

    def _cik_rows(self) -> list[tuple[str, str]]:
        """(ticker, cik) pairs from the A0 map; A0 must have run first."""
        path = get_output_path(self._asset_class, self._cik_map_file)
        try:
            df = load_csv(path)
        except Exception as exc:
            raise MarketDataIngestError(
                f"SEC events: CIK map {path} not readable ({exc}) — run A0 (cik_map) first") from exc
        return [(str(r["ticker"]).upper(), str(r["cik"]).lstrip("0").zfill(10)) for _, r in df.iterrows()]

    def run(self) -> pd.DataFrame:
        pairs = self._cik_rows()
        logger.info("SEC events: fetching 8-K stream for %d filers", len(pairs))
        frames = [df for df in (self._fetch(t, c) for t, c in pairs) if not df.empty]

        if not frames:
            raise MarketDataIngestError(
                "SEC events: no 8-K events retrieved for any filer — systemic fetch failure "
                "(User-Agent blocked / endpoint moved?)")
        combined = pd.concat(frames, ignore_index=True).sort_values(
            ["filing_date", "ticker"], ascending=[False, True]).reset_index(drop=True)
        if len(combined) < self._min_expected:
            raise MarketDataIngestError(
                f"SEC events: {len(combined)} events < floor {self._min_expected} — "
                "refusing to overwrite; suspected systemic fetch failure")

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        save_csv(combined, out_path)
        logger.info("SEC events: saved %d 8-K event(s) across %d filer(s) -> %s",
                    len(combined), combined["ticker"].nunique(), out_path)
        return combined

    def _fetch(self, ticker: str, cik: str) -> pd.DataFrame:
        endpoint = self._tpl.replace("{cik}", cik)
        try:
            recent = self._client.get(endpoint).json().get("filings", {}).get("recent", {})
        except Exception as exc:
            logger.warning("SEC events %s (CIK %s): %s", ticker, cik, exc)
            return pd.DataFrame()
        if not recent:
            return pd.DataFrame()

        raw = pd.DataFrame({
            "form": recent.get("form", []),
            "filing_date": recent.get("filingDate", []),
            "accession": recent.get("accessionNumber", []),
            "primary_document": recent.get("primaryDocument", []),
            "items": recent.get("items", []),
        })
        six_k = six_k_rows(recent, self._six_k_rules, self._allow, ticker=ticker, cik=cik,
                           start=self._start, end=self._end)
        if six_k:
            logger.info("SEC events %s: %d mapped 6-K event(s)", ticker, len(six_k))
        raw = raw[(raw["form"] == "8-K") & (raw["filing_date"] >= self._start) & (raw["filing_date"] <= self._end)]
        if raw.empty:
            return pd.DataFrame(six_k)

        out_rows: list[dict] = []
        cik_num = cik.lstrip("0")
        for _, r in raw.iterrows():
            codes = [c.strip() for c in str(r["items"]).split(",") if c.strip()]
            relevant = [c for c in codes if c in self._allow]
            if not relevant:
                continue                                    # no allowlisted item -> not an event we track
            weight = sum(self._allow[c] for c in relevant)
            acc = str(r["accession"])
            acc_nodash = acc.replace("-", "")
            out_rows.append({
                "ticker": ticker, "cik": cik, "filing_date": str(r["filing_date"]), "form": "8-K",
                "item_codes": ",".join(relevant), "accession": acc,
                "doc_url": f"{_EDGAR_VIEWER}/{cik_num}/{acc_nodash}/{acc_nodash}-index.htm",
                "weight": round(weight, 4),
            })
        if out_rows:
            logger.info("SEC events %s: %d allowlisted 8-K event(s)", ticker, len(out_rows))
        return pd.DataFrame(out_rows + six_k)


def classify_6k(primary_document: str, rules: Sequence[Mapping[str, str | None]]) -> str | None:
    """Map a Form 6-K to the 8-K item code it is equivalent to, from its primary-document file name.

    The first rule whose ``pattern`` matches the file name (``re.search``, case-insensitive) decides;
    a matching rule whose ``item`` is ``None`` marks the filing routine.

    Args:
        primary_document: The filing's EDGAR ``primaryDocument`` file name.
        rules: Ordered ``{"pattern": <regex>, "item": <8-K item code or None>}`` rules.

    Returns:
        The first matching rule's item code, or ``None`` when that rule is routine or no rule matches.

    Raises:
        ValueError: A rule's pattern is not a valid regular expression.
    """
    for rule in rules:
        try:
            hit = re.search(str(rule["pattern"]), primary_document, flags=re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"6-K rule pattern {rule['pattern']!r} is not a valid regex: {exc}") from exc
        if hit:
            item = rule.get("item")
            return None if item is None else str(item)
    return None


def six_k_rows(recent: Mapping[str, Sequence[str]], rules: Sequence[Mapping[str, str | None]],
               allowlist: Mapping[str, float], *, ticker: str, cik: str, start: str,
               end: str) -> list[dict[str, object]]:
    """Event rows for the Form 6-K filings in an EDGAR submissions ``filings.recent`` block.

    Args:
        recent: EDGAR's parallel arrays ``form``, ``filingDate``, ``accessionNumber`` and
            ``primaryDocument`` (other keys are ignored).
        rules: Ordered file-name rules, as for ``classify_6k``.
        allowlist: 8-K item code to event weight.
        ticker: The filer's ticker.
        cik: The filer's 10-digit zero-padded CIK.
        start: First filing date kept (ISO, inclusive).
        end: Last filing date kept (ISO, inclusive).

    Returns:
        One row per in-window 6-K mapped to an allowlisted item, in the block's order, with the 8-K
        row columns ``ticker, cik, filing_date, form, item_codes, accession, doc_url, weight``.

    Raises:
        ValueError: A rule's item is neither ``None`` nor in ``allowlist``.
    """
    bad = [r.get("item") for r in rules if r.get("item") is not None and str(r.get("item")) not in allowlist]
    if bad:                                   # a mistyped rule must not silently drop events
        raise ValueError(f"6-K rule item(s) {bad} not in the 8-K allowlist {sorted(allowlist)}")
    cik_num = cik.lstrip("0")
    rows: list[dict[str, object]] = []
    columns = (recent.get("form", []), recent.get("filingDate", []), recent.get("accessionNumber", []),
               recent.get("primaryDocument", []))
    for form, filed, acc, doc in zip(*columns, strict=False):
        if form != "6-K" or not start <= str(filed) <= end:
            continue
        item = classify_6k(str(doc), rules)
        if item is None:
            continue                          # routine or unmapped 6-K -> not an event we track
        acc_nodash = str(acc).replace("-", "")
        rows.append({
            "ticker": ticker, "cik": cik, "filing_date": str(filed), "form": "6-K",
            "item_codes": item, "accession": str(acc),
            "doc_url": f"{_EDGAR_VIEWER}/{cik_num}/{acc_nodash}/{acc_nodash}-index.htm",
            "weight": round(float(allowlist[item]), 4),
        })
    return rows
