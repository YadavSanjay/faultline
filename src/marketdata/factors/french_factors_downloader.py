"""Ken French Data Library daily style-factor downloader (market-intelligence F4).

Fetches the FF5 (Mkt-RF, SMB, HML, RMW, CMA, RF) and Momentum (MOM) daily ZIP archives, parses each
in memory, and merges them on date into one `factors/ff_factors.csv`.

Each archive is a single CSV wrapped in a multi-line text header (a paragraph describing the
construction) and footer (a copyright line), with the daily block in the middle: a column-name row,
then `YYYYMMDD,val,val,...` rows in PERCENT. Some FF files carry a second (annual/monthly) block
below the daily one; we take only the contiguous 8-digit-date daily rows. Parsed entirely from
bytes -> `io.BytesIO` -> `zipfile` (never extracted to a temp dir under data/ — dual-mode trap 1);
values are kept in the source's percent unit and `factor_history` converts to decimal.
"""

from __future__ import annotations

import io
import logging
import zipfile

import pandas as pd

from ..common.data_store import get_output_path, save_csv
from ..common.errors import MarketDataIngestError
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


def _parse_french_csv(text: str, value_cols: list[str]) -> pd.DataFrame:
    """Parse one French daily CSV: the contiguous ``YYYYMMDD,...`` block into (date, *value_cols).

    The header/footer prose is skipped by keying on the 8-digit date; parsing stops at the first
    non-data line after the block (blank line / copyright), so a trailing annual/monthly section is
    never mixed in.
    """
    rows: list[list[str]] = []
    started = False
    for line in text.splitlines():
        tok = line.split(",", 1)[0].strip()
        is_data = len(tok) == 8 and tok.isdigit()
        if is_data:
            started = True
            parts = [p.strip() for p in line.split(",")]
            rows.append(parts)
        elif started:
            break                          # end of the daily block
    if not rows:
        raise MarketDataIngestError("Ken French: no daily data rows found (format changed?)")

    out: dict[str, list] = {"date": []}
    for c in value_cols:
        out[c] = []
    for parts in rows:
        out["date"].append(parts[0])
        for i, c in enumerate(value_cols, start=1):
            out[c].append(pd.to_numeric(parts[i], errors="coerce") if i < len(parts) else None)
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    return df.dropna(subset=["date"]).reset_index(drop=True)


class FrenchFactorsDownloader:
    """Download + merge the Ken French daily FF5 and Momentum factors."""

    def __init__(self, config: dict, credentials: dict[str, str] | None = None, *, client: HttpClient | None = None):
        self._cfg = config["sources"]["ken_french"]
        self._asset_class = config["asset_class"]
        self._client = client or HttpClient(self._cfg, credentials or {})
        self._archives = self._cfg["archives"]
        self._out_dir = self._cfg.get("output_dir", "factors")
        self._out_file = self._cfg.get("output_file", "ff_factors.csv")

    def run(self) -> pd.DataFrame:
        merged: pd.DataFrame | None = None
        for key, spec in self._archives.items():
            df = self._fetch_archive(key, spec["endpoint"], spec["columns"])
            logger.info("Ken French %s: %d daily rows (%s -> %s)", key, len(df),
                        df["date"].min().date(), df["date"].max().date())
            merged = df if merged is None else merged.merge(df, on="date", how="outer")

        if merged is None or merged.empty:
            raise MarketDataIngestError("Ken French: no factor data downloaded")
        merged = merged.sort_values("date").reset_index(drop=True)
        merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")

        rel = f"{self._out_dir}/{self._out_file}" if self._out_dir else self._out_file
        out_path = get_output_path(self._asset_class, rel)
        save_csv(merged, out_path)
        logger.info("Ken French: wrote %d rows x %d factors -> %s",
                    len(merged), len(merged.columns) - 1, out_path)
        return merged

    def _fetch_archive(self, key: str, endpoint: str, columns: list[str]) -> pd.DataFrame:
        try:
            resp = self._client.get(endpoint)
            raw = resp.content
        except Exception as exc:
            raise MarketDataIngestError(f"Ken French {key}: fetch failed: {exc}") from exc
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
            member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            text = zf.read(member).decode("latin-1")
        except Exception as exc:
            raise MarketDataIngestError(f"Ken French {key}: not a readable ZIP/CSV: {exc}") from exc
        return _parse_french_csv(text, columns)
