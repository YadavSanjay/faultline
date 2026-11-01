"""
Nasdaq Data Link (formerly Quandl) EOD Price downloader.

Replaces Stooq as primary OHLCV source. Stooq now requires JavaScript rendering
and returns HTML for all automated requests — disabled.

Nasdaq Data Link EOD dataset returns one CSV per ticker containing:
  Date, Open, High, Low, Close, Volume, Dividend, Split,
  Adj_Open, Adj_High, Adj_Low, Adj_Close, Adj_Volume

Key advantages over alternatives:
  - Corporate actions (Dividend + Split columns) inline with prices
  - Adjusted prices already computed
  - Single API call covers full history + actions per ticker
  - Free tier: 50 calls/day, 50k/month (6 tickers well within limits)

API key: STOOQ_API_KEY in .env (Nasdaq Data Link / Quandl key format)

Endpoint:
  GET https://data.nasdaq.com/api/v3/datasets/EOD/{TICKER}/data.csv
      ?api_key={KEY}&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD

Output files (all long format, stacked across tickers):
  prices_daily.csv   — date, ticker, open, high, low, close, volume, adj_close
  dividends.csv      — date, ticker, amount  (only rows where Dividend > 0)
  splits.csv         — date, ticker, ratio    (only rows where Split != 1.0)
"""

import io
import logging
from datetime import timedelta

import pandas as pd

from ..common.data_store import get_output_path, load_csv, save_csv, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_COL_MAP = {
    "Date":       "date",
    "Open":       "open",
    "High":       "high",
    "Low":        "low",
    "Close":      "close",
    "Volume":     "volume",
    "Dividend":   "_dividend",
    "Split":      "_split",
    "Adj_Open":   "adj_open",
    "Adj_High":   "adj_high",
    "Adj_Low":    "adj_low",
    "Adj_Close":  "adj_close",
    "Adj_Volume": "adj_volume",
}


class NasdaqDownloader:
    """Download daily OHLCV + corporate actions from Nasdaq Data Link EOD dataset."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["nasdaq_data_link"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._portfolio = config["portfolio"]
        self._start = config["time_window"]["start_date"]
        self._end   = config["time_window"]["end_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["endpoint_template"]
        self._out = src_cfg["output_files"]

        self._prices_path = get_output_path(self._asset_class, self._out["prices"])
        self._div_path    = get_output_path(self._asset_class, self._out["dividends"])
        self._split_path  = get_output_path(self._asset_class, self._out["splits"])

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> dict[str, pd.DataFrame]:
        logger.info("Nasdaq DL: downloading EOD data for %d instruments", len(self._portfolio))

        existing_prices = load_csv(self._prices_path)
        price_frames, div_frames, split_frames = [], [], []

        for inst in self._portfolio:
            ticker = inst["ticker"]

            # Incremental: only fetch dates not already saved
            fetch_start = self._incremental_start(existing_prices, ticker)
            if fetch_start > self._end:
                logger.info("Nasdaq DL %s: up-to-date", ticker)
                continue

            prices, divs, splits = self._fetch_ticker(ticker, fetch_start, self._end)
            if not prices.empty:
                price_frames.append(prices)
            if not divs.empty:
                div_frames.append(divs)
            if not splits.empty:
                split_frames.append(splits)

        results: dict[str, pd.DataFrame] = {}

        if price_frames:
            new_prices = pd.concat(price_frames, ignore_index=True)
            upsert_csv(new_prices, self._prices_path,
                       date_col="date", key_cols=["date", "ticker"])
            logger.info("Nasdaq DL prices: %d new rows -> %s", len(new_prices), self._prices_path)
            results["prices"] = new_prices
        else:
            logger.info("Nasdaq DL: no new price data")
            results["prices"] = pd.DataFrame()

        # Corporate actions: always overwrite (small, authoritative)
        if div_frames:
            div_df = pd.concat(div_frames, ignore_index=True).sort_values(["ticker", "date"])
            save_csv(div_df, self._div_path)
            logger.info("Nasdaq DL dividends: %d rows -> %s", len(div_df), self._div_path)
            results["dividends"] = div_df
        else:
            results["dividends"] = pd.DataFrame()

        if split_frames:
            split_df = pd.concat(split_frames, ignore_index=True).sort_values(["ticker", "date"])
            save_csv(split_df, self._split_path)
            logger.info("Nasdaq DL splits: %d rows -> %s", len(split_df), self._split_path)
            results["splits"] = split_df
        else:
            logger.info("Nasdaq DL splits: none in history")
            results["splits"] = pd.DataFrame()

        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _incremental_start(self, existing: pd.DataFrame, ticker: str) -> str:
        if existing.empty or "ticker" not in existing.columns:
            return str(self._start)
        rows = existing[existing["ticker"] == ticker]
        if rows.empty:
            return str(self._start)
        last = pd.to_datetime(rows["date"]).max()
        return str((last + timedelta(days=1)).strftime("%Y-%m-%d"))

    def _fetch_ticker(
        self, ticker: str, start: str, end: str
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        endpoint = self._tpl.format(ticker=ticker, start_date=start, end_date=end)
        try:
            resp = self._client.get(endpoint)
            text = resp.text
        except Exception as exc:
            logger.warning("Nasdaq DL %s: %s", ticker, exc)
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        first = text.strip().split("\n")[0] if text.strip() else ""
        if not first.startswith("Date"):
            logger.warning("Nasdaq DL %s: unexpected response: %s", ticker, first[:80])
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        try:
            raw = pd.read_csv(io.StringIO(text))
        except Exception as exc:
            logger.warning("Nasdaq DL %s: CSV parse error: %s", ticker, exc)
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        raw = raw.rename(columns={c: _COL_MAP.get(c, c) for c in raw.columns})
        raw["date"] = pd.to_datetime(raw["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        raw = raw.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        raw.insert(1, "ticker", ticker)

        # ---- Price output ----
        price_cols = [c for c in
                      ["date", "ticker", "open", "high", "low", "close", "volume",
                       "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume"]
                      if c in raw.columns]
        prices = raw[price_cols].copy()
        for col in price_cols[2:]:          # numeric except date/ticker
            prices[col] = pd.to_numeric(prices[col], errors="coerce")

        # ---- Dividends (rows where _dividend > 0) ----
        divs = pd.DataFrame()
        if "_dividend" in raw.columns:
            raw["_dividend"] = pd.to_numeric(raw["_dividend"], errors="coerce").fillna(0)
            d = raw[raw["_dividend"] > 0][["date", "ticker", "_dividend"]].copy()
            d = d.rename(columns={"_dividend": "amount"})
            divs = d

        # ---- Splits (rows where _split != 1.0) ----
        splits = pd.DataFrame()
        if "_split" in raw.columns:
            raw["_split"] = pd.to_numeric(raw["_split"], errors="coerce").fillna(1.0)
            s = raw[raw["_split"] != 1.0][["date", "ticker", "_split"]].copy()
            s = s.rename(columns={"_split": "ratio"})
            splits = s

        logger.info("Nasdaq DL %s: %d price rows, %d dividends, %d splits  (%s->%s)",
                    ticker, len(prices), len(divs), len(splits),
                    prices["date"].iloc[0] if not prices.empty else "?",
                    prices["date"].iloc[-1] if not prices.empty else "?")
        return prices, divs, splits
