"""Yahoo long-history downloader: daily adjusted closes back to the 1990s for severity anchors.

The regular Yahoo source (``yahoo_downloader.py``) pulls 10 years of OHLCV plus dividends and splits
into ``prices_daily.csv``/``dividends.csv``. This source only reads adjusted closes from a configured
``start_date`` (default 1990) for the book's names and the long-history indices (``^SOX``, ``^IXIC``,
``^GSPC``, ``XLU``), into its own file, so it can never clobber the pricing inputs. A required ticker
that fails is fail-loud (``MarketDataIngestError``); any other failure is skipped with a warning.
"""

from __future__ import annotations

import calendar
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import pandas as pd

from src.marketdata.common.data_store import get_output_path, save_csv
from src.marketdata.common.errors import MarketDataIngestError
from src.marketdata.common.http_client import HttpClient, HttpClientError

logger = logging.getLogger(__name__)

_COLUMNS = ["date", "ticker", "adj_close"]


def parse_chart(payload: dict[str, Any], ticker: str) -> pd.DataFrame:
    """Parses one Yahoo chart response into ``date, ticker, adj_close`` rows.

    Args:
        payload: The decoded chart JSON (``chart.result[0]``).
        ticker: The configured ticker written into the ``ticker`` column.

    Returns:
        Rows sorted by date; each date is the point's UTC timestamp shifted by ``meta.gmtoffset``
        (the exchange's local trading date). Null closes are dropped; a repeated date keeps the
        last point.

    Raises:
        ValueError: The payload carries ``chart.error`` or has no result.
    """
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"Yahoo chart error for {ticker}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"Yahoo chart for {ticker} has no result")
    res = results[0]
    offset = int((res.get("meta") or {}).get("gmtoffset") or 0)
    timestamps = res.get("timestamp") or []
    adj = ((res.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    by_date: dict[str, float] = {}
    for ts, value in zip(timestamps, adj, strict=False):
        if value is None:
            continue
        local = datetime.fromtimestamp(int(ts) + offset, tz=UTC).date().isoformat()
        by_date[local] = float(value)                       # a repeated date keeps the last point
    rows = [(d, ticker, v) for d, v in sorted(by_date.items())]
    return pd.DataFrame(rows, columns=_COLUMNS)


class YahooHistoryDownloader:
    """Long daily adjusted-close history for the configured tickers (``sources.yahoo_history``)."""

    def __init__(self, config: dict, credentials: dict[str, str], *, client: Any = None) -> None:
        """Reads ``config["sources"]["yahoo_history"]``; builds the project ``HttpClient`` if needed.

        Args:
            config: The equity market-data config.
            credentials: Source credentials (Yahoo needs none).
            client: An HTTP client with ``get(endpoint)``; ``None`` builds ``HttpClient``.
        """
        self._cfg = config["sources"]["yahoo_history"]
        self._asset_class = config["asset_class"]
        self._client = client if client is not None else HttpClient(self._cfg, credentials)
        self._required = set(self._cfg.get("required", []))
        self._min_rows = int(self._cfg.get("min_rows", 1))

    def _period1(self) -> int:
        start = date.fromisoformat(str(self._cfg["start_date"]))
        return calendar.timegm(start.timetuple())

    def collect(self) -> pd.DataFrame:
        """Fetches and parses every configured ticker.

        Returns:
            ``date, ticker, adj_close`` rows of every ticker that succeeded.

        Raises:
            MarketDataIngestError: A required ticker failed or fell below ``min_rows``.
        """
        period1 = self._period1()
        period2 = calendar.timegm((datetime.now(UTC).date() + timedelta(days=1)).timetuple())
        frames: list[pd.DataFrame] = []
        for spec in self._cfg["tickers"]:
            ticker, symbol = str(spec["ticker"]), str(spec.get("yahoo", spec["ticker"]))
            endpoint = self._cfg["endpoint_template"].format(
                yahoo_ticker=quote(symbol, safe=""), period1=period1, period2=period2)
            try:
                df = parse_chart(self._client.get(endpoint).json(), ticker)
                if len(df) < self._min_rows:
                    raise ValueError(f"{len(df)} rows < min_rows {self._min_rows}")
            except (HttpClientError, ValueError) as exc:
                if ticker in self._required:
                    raise MarketDataIngestError(
                        f"yahoo_history: required ticker {ticker} ({symbol}) failed: {exc}") from exc
                logger.warning("yahoo_history: skipping %s (%s): %s", ticker, symbol, exc)
                continue
            logger.info("yahoo_history %s: %d rows from %s", ticker, len(df), df["date"].iloc[0])
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=_COLUMNS)
        return pd.concat(frames, ignore_index=True).sort_values(["ticker", "date"], ignore_index=True)

    def run(self) -> pd.DataFrame:
        """``collect()`` and write the output file (full overwrite) with its provenance sidecar."""
        df = self.collect()
        save_csv(df, get_output_path(self._asset_class, self._cfg["output_file"]))
        return df
