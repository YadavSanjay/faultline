"""Targeted, additive fetch of option-underlier spot history (risk GAP 2).

Pulls daily OHLCV for the equity-option underliers that lacked spot history
(AAPL, MSFT, META, TSLA, QQQ, SPX) from Yahoo and **appends only those tickers**
to data/marketdata/equity/prices_daily.csv (+ prices_adj_close.csv).

It deliberately does NOT use the full equity runner: that would re-fetch the
existing cash-book tickers (perturbing pinned pricing inputs) and overwrite
dividends.csv / splits.csv. Here we upsert only the new tickers, whose (date,
ticker) keys don't collide with existing rows, so nothing already in the store
changes — keeping the golden-PV inputs stable.

    python -m scripts.marketdata.fetch_option_underliers
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.marketdata.common.config_loader import load_config
from src.marketdata.common.data_store import get_output_path, upsert_csv
from src.marketdata.equity.runner import _load_credentials
from src.marketdata.equity.yahoo_downloader import YahooDownloader

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("fetch_option_underliers")

NEW_TICKERS = {"AAPL", "MSFT", "META", "TSLA", "QQQ", "SPX"}


def main() -> None:
    config = load_config("equity")
    dl = YahooDownloader(config, _load_credentials())
    targets = [i for i in config["portfolio"] if i["ticker"] in NEW_TICKERS]
    logger.info("Fetching %d option-underlier(s): %s",
                len(targets), [i["ticker"] for i in targets])

    prices, adj = [], []
    for inst in targets:
        p, _divs, _splits, a = dl._fetch_ticker(inst["ticker"], inst["yahoo"])
        if not p.empty:
            prices.append(p)
        if not a.empty:
            adj.append(a)

    if not prices:
        logger.warning("No data fetched — nothing written.")
        return

    import pandas as pd
    upsert_csv(pd.concat(prices, ignore_index=True),
               get_output_path("equity", "prices_daily.csv"),
               date_col="date", key_cols=["date", "ticker"])
    if adj:
        upsert_csv(pd.concat(adj, ignore_index=True),
                   get_output_path("equity", "prices_adj_close.csv"),
                   date_col="date", key_cols=["date", "ticker"])
    logger.info("Done. Added tickers: %s", sorted({p['ticker'].iloc[0] for p in prices}))


if __name__ == "__main__":
    main()
