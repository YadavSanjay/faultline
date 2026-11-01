"""Cash-equity market-data adapter.

Reads the daily close from `data/marketdata/equity/prices_daily.csv` (latest
on/before the valuation date) and the trailing-12-month dividend yield from
`dividends.csv`. Pure pandas — no QuantLib.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.common import storage

from ..input.common.market_data import EquitySpotData
from ..input.common.valuation import ValuationContext
from .snapshot import MarketDataSnapshot, SnapshotReader
from .staleness import pick_snapshot


class CashEquityMarketDataAdapter(SnapshotReader):
    def __init__(self, config: dict, project_root: Path,
                 snapshot: MarketDataSnapshot | None = None):
        self.cfg = config.get("cash_equity", {})
        self.root = project_root
        self.data_dir = storage.join(storage.data_base(project_root), "marketdata", "equity")
        self._snapshot = snapshot

    def equity_spot(self, ticker: str, ctx: ValuationContext) -> tuple[EquitySpotData, date, list[str]]:
        px_path = storage.join(self.data_dir, self.cfg.get("prices_file", "prices_daily.csv"))
        if not storage.exists(px_path):
            raise FileNotFoundError(f"Equity prices not found: {px_path}")

        px = self._read_csv(px_path, parse_dates=["date"])
        px = px[px["ticker"] == ticker]
        if px.empty:
            raise KeyError(f"No price history for ticker {ticker!r} in {px_path}")

        chosen, warnings = pick_snapshot(px["date"], ctx, label=f"{ticker} price",
                                         kind="equity_spot")
        row = px[px["date"] == chosen].iloc[-1]
        as_of = chosen.date()
        spot = float(row["close"])

        div_yield = self._trailing_div_yield(ticker, chosen, spot)

        return (
            EquitySpotData(ticker=ticker, currency="USD", spot=spot,
                           as_of_date=as_of, dividend_yield=div_yield),
            as_of, warnings,
        )

    def _trailing_div_yield(self, ticker: str, as_of: pd.Timestamp, spot: float) -> float:
        path = storage.join(self.data_dir, self.cfg.get("dividends_file", "dividends.csv"))
        if not storage.exists(path) or spot <= 0:
            return 0.0
        d = self._read_csv(path, parse_dates=["date"])
        lookback = int(self.cfg.get("dividend_lookback_days", 365))
        d = d[(d["ticker"] == ticker)
              & (d["date"] > as_of - pd.Timedelta(days=lookback))
              & (d["date"] <= as_of)]
        return float(d["amount"].sum() / spot)
