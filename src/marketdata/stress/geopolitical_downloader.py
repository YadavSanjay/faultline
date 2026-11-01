"""Geopolitical-risk (GPR) + economic-policy-uncertainty (EPU) downloaders (F8, P7).

Gives the Taiwan scenario the *quantitative* trigger its narrative already names but has no data
behind (design FINDING 8). Two free sources, no key:

  * **GPR** (Caldara-Iacoviello) — the daily **global** index (``GPRD``, ``GPRD_ACT``/``THREAT``)
    plus the monthly **country** file (``GPRC_TWN``/``GPRC_CHN`` — Taiwan/China). NOTE: contrary to
    the design's assumption, the country sub-indices are **monthly**, not daily (the daily file is
    global only), so both files are ingested — daily-global for a timely level, monthly-country for
    the Taiwan-specific calibration. Both are binary ``.xls`` -> **trap 2** (bytes -> ``BytesIO`` ->
    ``read_excel``, never a raw-path read).
  * **EPU** — the daily US economic-policy-uncertainty index (a plain CSV).

Outputs: ``stress/gpr_daily.csv`` (date, GPRD, GPRD_ACT, GPRD_THREAT), ``stress/gpr_country.csv``
(month, GPRC_TWN, GPRC_CHN, GPRHC_TWN, GPRHC_CHN), ``stress/epu_daily.csv`` (date, epu). Fail-loud
below a floor for GPR (the scenario trigger depends on it); EPU is graceful context.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from ..common.data_store import get_output_path, save_csv
from ..common.errors import MarketDataIngestError
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class GPRDownloader:
    """Fetch the GPR daily-global + monthly-country workbooks (F8)."""

    def __init__(self, config: dict, credentials: dict[str, str], *, client: HttpClient | None = None):
        src_cfg = config["sources"]["gpr"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._client = client if client is not None else HttpClient(src_cfg, credentials)
        self._daily_ep = src_cfg["daily_endpoint"]
        self._country_ep = src_cfg["country_endpoint"]
        self._country_cols = list(src_cfg.get("country_columns",
                                              ["GPRC_TWN", "GPRC_CHN", "GPRHC_TWN", "GPRHC_CHN"]))
        self._min_rows = int(src_cfg.get("min_expected_rows", 100))
        of = src_cfg["output_files"]
        self._daily_out = of["daily"]
        self._country_out = of["country"]

    def _excel(self, endpoint: str) -> pd.DataFrame:
        # trap 2: binary .xls -> bytes -> BytesIO -> read_excel (dual-mode safe)
        raw = self._client.get(endpoint).content
        return pd.read_excel(io.BytesIO(raw), sheet_name=0)

    def run(self) -> pd.DataFrame:
        logger.info("GPR: fetching daily-global + monthly-country indices")
        try:
            daily = self._excel(self._daily_ep)
            country = self._excel(self._country_ep)
        except Exception as exc:
            raise MarketDataIngestError(f"GPR: workbook fetch/parse failed ({exc})") from exc

        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        d = d.dropna(subset=["date"])[["date", "GPRD", "GPRD_ACT", "GPRD_THREAT"]]
        d = d.dropna(subset=["GPRD"]).drop_duplicates("date").sort_values("date")
        if len(d) < self._min_rows:
            raise MarketDataIngestError(
                f"GPR: {len(d)} daily rows < floor {self._min_rows} — suspected fetch failure")

        c = country.copy()
        c["month"] = pd.to_datetime(c["month"], errors="coerce").dt.strftime("%Y-%m-%d")
        keep = ["month", *[col for col in self._country_cols if col in c.columns]]
        c = c.dropna(subset=["month"])[keep].drop_duplicates("month").sort_values("month")

        daily_path = get_output_path(self._asset_class, self._daily_out)
        country_path = get_output_path(self._asset_class, self._country_out)
        save_csv(d, daily_path)
        save_csv(c, country_path)
        logger.info("GPR: saved %d daily -> %s ; %d monthly-country -> %s",
                    len(d), daily_path, len(c), country_path)
        return d


class EPUDownloader:
    """Fetch the daily US economic-policy-uncertainty index (F8, graceful context)."""

    def __init__(self, config: dict, credentials: dict[str, str], *, client: HttpClient | None = None):
        src_cfg = config["sources"]["epu"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._client = client if client is not None else HttpClient(src_cfg, credentials)
        self._endpoint = src_cfg["endpoint"]

    def run(self) -> pd.DataFrame:
        logger.info("EPU: fetching daily policy-uncertainty index")
        try:
            text = self._client.get(self._endpoint).text
            df = pd.read_csv(io.StringIO(text))
        except Exception as exc:
            logger.warning("EPU: fetch/parse failed (%s) — skipping (graceful context)", exc)
            return pd.DataFrame()
        need = {"year", "month", "day", "daily_policy_index"}
        if not need.issubset(df.columns):
            logger.warning("EPU: unexpected columns %s — skipping", list(df.columns))
            return pd.DataFrame()
        df = df.dropna(subset=["daily_policy_index"])
        df["date"] = pd.to_datetime(
            {"year": df["year"].astype(int), "month": df["month"].astype(int),
             "day": df["day"].astype(int)},
            errors="coerce").dt.strftime("%Y-%m-%d")
        out = df.dropna(subset=["date"])[["date", "daily_policy_index"]].rename(
            columns={"daily_policy_index": "epu"}).drop_duplicates("date").sort_values("date")
        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        save_csv(out, out_path)
        logger.info("EPU: saved %d rows -> %s", len(out), out_path)
        return out
