"""
CBC (Taiwan) USD/TWD closing-rate downloader — NDF settlement fixing (trade #26).

The Central Bank of the Republic of China (Taiwan) publishes the daily NT$/US$
interbank closing rate at lp-700-2.html. This is the rate USD/TWD NDFs settle
against in practice — i.e. the NDF fixing for trade #26. No CSV/JSON API exists
(EMTA NDF templates retired, TAIFEX dailyFXRates 404), so the paginated HTML is
the only free source.

Pagination: 20 rows per page, URL pattern lp-700-2-{page}-20.html with page 1 =
most recent. ~166 pages reach back to ~2013. We walk pages newest -> oldest and
stop once we pass the configured start_date or hit a page already fully downloaded
(incremental runs only fetch the few newest pages).

Date strings are YYYY/MM/DD.

Output: cbc_twd_ndf_fix.csv
    date, usdtwd_cbc
"""

import io
import logging

import pandas as pd

from ..common.data_store import (
    already_downloaded_dates,
    get_output_path,
    upsert_csv,
)
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class CBCTwdDownloader:
    """Download CBC USD/TWD interbank closing rate (NDF fixing), paginated."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["cbc_twd_spot"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._end = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._page_tpl = src_cfg["page_template"]
        self._max_pages = int(src_cfg.get("max_pages", 200))
        self._out_path = get_output_path(self._asset_class, src_cfg["output_file"])

    def run(self) -> pd.DataFrame:
        known = already_downloaded_dates(self._out_path)
        logger.info("CBC TWD: %d dates already downloaded; walking pages newest->oldest",
                    len(known))

        frames: list[pd.DataFrame] = []
        for page in range(1, self._max_pages + 1):
            df = self._fetch_page(page)
            if df.empty:
                logger.info("CBC TWD: page %d empty — stopping", page)
                break

            page_dates = set(df["date"])
            min_date = min(page_dates)
            frames.append(df)

            # Stop once we've paged past the start of our window.
            if min_date < self._start:
                logger.info("CBC TWD: page %d reached %s (< start %s) — stopping",
                            page, min_date, self._start)
                break

            # Incremental: once a whole page is already on disk, everything older is too.
            if known and page_dates.issubset(known):
                logger.info("CBC TWD: page %d fully already downloaded — stopping", page)
                break

        if not frames:
            logger.warning("CBC TWD: no data retrieved")
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates("date").sort_values("date")
        combined = combined[
            (combined["date"] >= self._start) & (combined["date"] <= self._end)
        ]

        upsert_csv(combined, self._out_path, date_col="date")
        logger.info("CBC TWD: saved %d rows -> %s", len(combined), self._out_path)
        return combined

    def _fetch_page(self, page: int) -> pd.DataFrame:
        endpoint = self._page_tpl.format(page=page)
        try:
            resp = self._client.get(endpoint)
            tables = pd.read_html(io.StringIO(resp.text), flavor="lxml")
        except Exception as exc:
            logger.warning("CBC TWD page %d: %s", page, exc)
            return pd.DataFrame()

        if not tables or tables[0].shape[1] < 2:
            return pd.DataFrame()

        df = tables[0].iloc[:, :2]
        df.columns = ["date", "usdtwd_cbc"]
        df["usdtwd_cbc"] = pd.to_numeric(df["usdtwd_cbc"], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], format="%Y/%m/%d",
                                    errors="coerce").dt.strftime("%Y-%m-%d")
        return df.dropna(subset=["date", "usdtwd_cbc"]).drop_duplicates("date")
