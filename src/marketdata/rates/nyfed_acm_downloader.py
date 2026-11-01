"""NY Fed ACM term-premium downloader (F6, market-intelligence P5).

The Adrian-Crump-Moench model decomposes the nominal Treasury yield into an expectations
component and a **term premium**. FINDING 6: the ``rate_capital`` scenario's stated driver is a
"+150bp 10s30s steepener (term premium)" — a term-premium narrative with no term-premium data
behind it. This ingests the 10Y term-premium series so that driver is quantitatively grounded.

Source is a single binary ``.xls`` workbook (sheets ``ACM Monthly`` / ``ACM Daily``, columns
``ACMTP01``..``ACMTP10``). **Trap 2:** the file is binary, so it is read as bytes through the
storage/HTTP layer and parsed via ``io.BytesIO`` -> ``pd.read_excel`` — never ``pd.read_excel(path)``
on a raw path, which breaks on ``s3://`` under the dual-mode facade. Output:
``rates/nyfed_acm_term_premium.csv`` (date + ACMTP10, in percent).
"""

import io
import logging

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class NYFedACMDownloader:
    """Fetch the ACM workbook and extract the daily 10Y term-premium series."""

    def __init__(self, config: dict, credentials: dict[str, str], *, client: HttpClient | None = None):
        src_cfg = config["sources"]["nyfed_acm"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        self._client = client or HttpClient(src_cfg, credentials)
        self._endpoint = src_cfg["endpoint"]
        self._sheet = src_cfg.get("sheet", "ACM Daily")
        self._columns = list(src_cfg.get("columns", ["ACMTP10"]))

    def run(self) -> pd.DataFrame:
        logger.info("NY Fed ACM: downloading term-premium workbook from %s", self._endpoint)
        resp = self._client.get(self._endpoint)
        # Trap 2: binary .xls -> bytes -> BytesIO -> read_excel (dual-mode safe; no raw-path read).
        raw = resp.content
        df = pd.read_excel(io.BytesIO(raw), sheet_name=self._sheet)

        keep = ["DATE", *[c for c in self._columns if c in df.columns]]
        missing = [c for c in self._columns if c not in df.columns]
        if missing:
            logger.warning("NY Fed ACM: columns not found in sheet %r: %s", self._sheet, missing)
        out = df[keep].copy()
        out = out.rename(columns={"DATE": "date"})
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        for c in self._columns:
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce")
        out = out.dropna(subset=["date"]).drop_duplicates("date").sort_values("date")
        out = out[(out["date"] >= self._start) & (out["date"] <= self._end)]

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(out, out_path, date_col="date")
        logger.info("NY Fed ACM: saved %d rows (%s) -> %s", len(out), ",".join(self._columns), out_path)
        return out
