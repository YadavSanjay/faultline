"""FRED long-history downloader: decades of rates, VIX, credit and FX for severity anchors.

Pulls full-history ``fredgraph.csv`` series (Treasury yields, ``VIXCLS``, Moody's ``BAA10Y`` — the free
long credit proxy, since the ICE BofA OAS is only served ~3y back — and ``DEXJPUS``/``DEXTAUS``) into one
wide file of its own, from a configured ``start_date``. Separate from ``fred_stress_macro.csv`` and
``fred_rates.csv`` so the VaR and pricing inputs are untouched. A required series that fails is
fail-loud (``MarketDataIngestError``); any other failure is skipped with a warning.
"""

from __future__ import annotations

import io
import logging
from functools import reduce
from typing import Any

import pandas as pd

from src.marketdata.common.data_store import get_output_path, save_csv
from src.marketdata.common.errors import MarketDataIngestError
from src.marketdata.common.http_client import HttpClient, HttpClientError

logger = logging.getLogger(__name__)


def parse_fredgraph(text: str, series_id: str) -> pd.Series:
    """Parses one ``fredgraph.csv`` response into a float series.

    Args:
        text: The CSV body (a header line, then ``date,value`` rows).
        series_id: The series id, used as the series name.

    Returns:
        Values indexed by ``YYYY-MM-DD`` strings, without FRED's ``"."`` marker or blanks.
    """
    df = pd.read_csv(io.StringIO(text), names=["date", "value"], skiprows=1, dtype=str,
                     keep_default_na=False)
    values = pd.to_numeric(df["value"].str.strip(), errors="coerce")
    dates = pd.to_datetime(df["date"].str.strip(), errors="coerce")
    keep = values.notna() & dates.notna()
    s = pd.Series(values[keep].to_numpy(dtype=float),
                  index=dates[keep].dt.strftime("%Y-%m-%d").to_numpy(), name=series_id)
    return s[~s.index.duplicated(keep="last")].sort_index()


class FredLongDownloader:
    """Long daily history of the configured FRED series (``sources.fred_long``)."""

    def __init__(self, config: dict, credentials: dict[str, str], *, client: Any = None) -> None:
        """Reads ``config["sources"]["fred_long"]``; builds the project ``HttpClient`` if needed.

        Args:
            config: The stress market-data config.
            credentials: Source credentials (FRED's fredgraph needs none).
            client: An HTTP client with ``get(endpoint)``; ``None`` builds ``HttpClient``.
        """
        self._cfg = config["sources"]["fred_long"]
        self._asset_class = config["asset_class"]
        self._client = client if client is not None else HttpClient(self._cfg, credentials)
        self._required = set(self._cfg.get("required", []))
        self._min_rows = int(self._cfg.get("min_rows", 1))
        self._start = str(self._cfg["start_date"])

    def collect(self) -> pd.DataFrame:
        """Fetches every configured series into one wide frame from ``start_date``.

        Returns:
            A ``date`` column plus one column per series that succeeded, sorted by date.

        Raises:
            MarketDataIngestError: A required series failed or fell below ``min_rows``.
        """
        cols: list[pd.Series] = []
        for series_id in self._cfg["series"]:
            sid = str(series_id)
            try:
                s = parse_fredgraph(self._client.get(self._cfg["endpoint_template"].format(series_id=sid)).text,
                                    sid)
                s = s[s.index >= self._start]
                if len(s) < self._min_rows:
                    raise ValueError(f"{len(s)} rows < min_rows {self._min_rows}")
            except (HttpClientError, ValueError) as exc:
                if sid in self._required:
                    raise MarketDataIngestError(f"fred_long: required series {sid} failed: {exc}") from exc
                logger.warning("fred_long: skipping %s: %s", sid, exc)
                continue
            logger.info("fred_long %s: %d rows from %s", sid, len(s), s.index[0])
            cols.append(s)
        if not cols:
            return pd.DataFrame(columns=["date"])
        wide = reduce(lambda a, b: a.join(b, how="outer"), [c.to_frame() for c in cols])
        wide = wide.sort_index()
        wide.index.name = "date"
        return wide.reset_index()

    def run(self) -> pd.DataFrame:
        """``collect()`` and write the output file (full overwrite) with its provenance sidecar."""
        df = self.collect()
        save_csv(df, get_output_path(self._asset_class, self._cfg["output_file"]))
        return df
