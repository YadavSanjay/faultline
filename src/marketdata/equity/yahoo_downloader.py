"""
Yahoo Finance corporate actions + OHLCV downloader.

Stooq's CSV endpoint now returns JS-rendered HTML for all automated requests.
Yahoo Finance chart API is the confirmed primary source for both OHLCV prices
and corporate actions (dividends + splits) in a single call.

Endpoint (requires real browser User-Agent — bare curl returns HTTP 401):
  GET /v8/finance/chart/{TICKER}?interval=1d&range=10y&events=div,split

Response JSON structure:
  chart.result[0].timestamp[]                        — Unix epoch seconds
  chart.result[0].indicators.quote[0]                — {open, high, low, close, volume}
  chart.result[0].indicators.adjclose[0].adjclose[]  — adjusted close
  chart.result[0].events.dividends                   — {<epoch>: {amount, date}}
  chart.result[0].events.splits                      — {<epoch>: {date, numerator, denominator}}

Four output files (all long/stacked format across tickers):
  prices_daily.csv     — date, ticker, open, high, low, close, volume, adj_close
  dividends.csv        — date, ticker, amount  (→ QL DiscreteDividendSchedule)
  splits.csv           — date, ticker, numerator, denominator, ratio
  prices_adj_close.csv — date, ticker, adj_close  (kept separately for compatibility)
"""

import logging
from datetime import UTC, datetime

import pandas as pd

from ..common.data_store import get_output_path, save_csv, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


def _epoch_to_date(epoch: int | str) -> str:
    return datetime.fromtimestamp(int(epoch), tz=UTC).strftime("%Y-%m-%d")


class YahooDownloader:
    """Fetch OHLCV + corporate actions from Yahoo Finance chart API."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["yahoo_events"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._portfolio = config["portfolio"]
        self._start = config["time_window"]["start_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["endpoint_template"]
        self._out = src_cfg["output_files"]

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> dict[str, pd.DataFrame]:
        price_frames, div_frames, split_frames, adj_frames = [], [], [], []

        for inst in self._portfolio:
            ticker = inst["ticker"]
            yahoo  = inst["yahoo"]
            prices, divs, splits, adj = self._fetch_ticker(ticker, yahoo)
            if not prices.empty:
                price_frames.append(prices)
            if not divs.empty:
                div_frames.append(divs)
            if not splits.empty:
                split_frames.append(splits)
            if not adj.empty:
                adj_frames.append(adj)

        results: dict[str, pd.DataFrame] = {}

        # OHLCV prices — incremental upsert, key = (date, ticker)
        if price_frames:
            price_df = pd.concat(price_frames, ignore_index=True)
            p = get_output_path(self._asset_class, self._out["prices"])
            upsert_csv(price_df, p, date_col="date", key_cols=["date", "ticker"])
            logger.info("Yahoo prices: %d rows -> %s", len(price_df), p)
            results["prices"] = price_df
        else:
            results["prices"] = pd.DataFrame()

        # Dividends — always overwrite (full history, small payload)
        if div_frames:
            div_df = pd.concat(div_frames, ignore_index=True).sort_values(["ticker", "date"])
            p = get_output_path(self._asset_class, self._out["dividends"])
            save_csv(div_df, p)
            logger.info("Yahoo dividends: %d rows -> %s", len(div_df), p)
            results["dividends"] = div_df
        else:
            results["dividends"] = pd.DataFrame()

        # Splits — always overwrite
        if split_frames:
            split_df = pd.concat(split_frames, ignore_index=True).sort_values(["ticker", "date"])
            p = get_output_path(self._asset_class, self._out["splits"])
            save_csv(split_df, p)
            logger.info("Yahoo splits: %d rows -> %s", len(split_df), p)
            results["splits"] = split_df
        else:
            logger.info("Yahoo splits: none found")
            results["splits"] = pd.DataFrame()

        # Adjusted close — separate file for compatibility
        if adj_frames:
            adj_df = pd.concat(adj_frames, ignore_index=True)
            p = get_output_path(self._asset_class, self._out["adj_close"])
            upsert_csv(adj_df, p, date_col="date", key_cols=["date", "ticker"])
            logger.info("Yahoo adj_close: %d rows -> %s", len(adj_df), p)
            results["adj_close"] = adj_df
        else:
            results["adj_close"] = pd.DataFrame()

        return results

    # ------------------------------------------------------------------
    # Per-ticker fetch
    # ------------------------------------------------------------------

    def _fetch_ticker(
        self, ticker: str, yahoo_sym: str
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        endpoint = self._tpl.format(yahoo_ticker=yahoo_sym)
        try:
            resp = self._client.get(endpoint)
            data = resp.json()
        except Exception as exc:
            logger.warning("Yahoo %s: %s", ticker, exc)
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        try:
            result = data["chart"]["result"][0]
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("Yahoo %s: unexpected JSON structure: %s", ticker, exc)
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        prices = self._parse_ohlcv(result, ticker)
        divs   = self._parse_dividends(result, ticker)
        splits = self._parse_splits(result, ticker)
        adj    = self._parse_adj_close(result, ticker)

        logger.info("Yahoo %s: %d prices, %d dividends, %d splits",
                    ticker, len(prices), len(divs), len(splits))
        return prices, divs, splits, adj

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_ohlcv(self, result: dict, ticker: str) -> pd.DataFrame:
        timestamps = result.get("timestamp", [])
        q = result.get("indicators", {}).get("quote", [{}])[0]
        adj_data = (
            result.get("indicators", {})
                  .get("adjclose", [{}])[0]
                  .get("adjclose", [])
        )
        if not timestamps:
            return pd.DataFrame()

        def _floats(lst: list) -> list[float | None]:
            return [float(v) if v is not None else None for v in lst]

        rows = {
            "date":      [_epoch_to_date(t) for t in timestamps],
            "ticker":    ticker,
            "open":      _floats(q.get("open",   [None] * len(timestamps))),
            "high":      _floats(q.get("high",   [None] * len(timestamps))),
            "low":       _floats(q.get("low",    [None] * len(timestamps))),
            "close":     _floats(q.get("close",  [None] * len(timestamps))),
            "volume":    q.get("volume", [None] * len(timestamps)),
            "adj_close": _floats(adj_data) if adj_data else [None] * len(timestamps),
        }
        df = pd.DataFrame(rows)
        df = df.dropna(subset=["close"])
        return df[df["date"] >= self._start].sort_values("date").reset_index(drop=True)

    def _parse_dividends(self, result: dict, ticker: str) -> pd.DataFrame:
        raw = result.get("events", {}).get("dividends", {})
        if not raw:
            return pd.DataFrame()
        rows = [
            {"date": _epoch_to_date(epoch), "ticker": ticker,
             "amount": float(ev.get("amount", 0))}
            for epoch, ev in raw.items()
        ]
        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        return df[df["date"] >= self._start]

    def _parse_splits(self, result: dict, ticker: str) -> pd.DataFrame:
        raw = result.get("events", {}).get("splits", {})
        if not raw:
            return pd.DataFrame()
        rows = [
            {
                "date":        _epoch_to_date(epoch),
                "ticker":      ticker,
                "numerator":   int(ev.get("numerator", 1)),
                "denominator": int(ev.get("denominator", 1)),
                "ratio":       float(ev.get("numerator", 1)) /
                               max(float(ev.get("denominator", 1)), 1e-9),
            }
            for epoch, ev in raw.items()
        ]
        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        return df[df["date"] >= self._start]

    def _parse_adj_close(self, result: dict, ticker: str) -> pd.DataFrame:
        timestamps = result.get("timestamp", [])
        adj = (
            result.get("indicators", {})
                  .get("adjclose", [{}])[0]
                  .get("adjclose", [])
        )
        if not timestamps or not adj:
            return pd.DataFrame()
        df = pd.DataFrame({
            "date":      [_epoch_to_date(t) for t in timestamps],
            "ticker":    ticker,
            "adj_close": [float(v) if v is not None else float("nan") for v in adj],
        })
        df = df.dropna(subset=["adj_close"])
        return df[df["date"] >= self._start].sort_values("date").reset_index(drop=True)
