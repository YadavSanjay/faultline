"""
MOVE index downloader — ICE BofAML MOVE (rates implied volatility).

The MOVE index is the rates-vol level for the swaption/cap vega shock (#23-25)
in Methodology B's bust leg. MOVE is not free on FRED, so we use Yahoo Finance's
chart endpoint (symbol ^MOVE -> URL-encoded %5EMOVE), which requires a real
browser User-Agent (a bare request returns HTTP 401).

Response JSON (same shape as the equity Yahoo downloader):
  chart.result[0].timestamp[]                       — Unix epoch seconds
  chart.result[0].indicators.quote[0]               — {open, high, low, close, volume}
  chart.result[0].indicators.adjclose[0].adjclose[] — adjusted close (= close for an index)

Output: move_index.csv  — date, open, high, low, close
"""

import logging
from datetime import UTC, datetime

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


def _epoch_to_date(epoch: int | str) -> str:
    return datetime.fromtimestamp(int(epoch), tz=UTC).strftime("%Y-%m-%d")


class MOVEDownloader:
    """Fetch the ^MOVE rates-vol index from Yahoo Finance chart API."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["move_yahoo"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._start = config["time_window"]["start_date"]
        self._client = HttpClient(src_cfg, credentials)
        self._endpoint = src_cfg["endpoint_template"].format(symbol=src_cfg["symbol"])

    def run(self) -> pd.DataFrame:
        try:
            resp = self._client.get(self._endpoint)
            data = resp.json()
        except Exception as exc:
            logger.warning("MOVE: %s", exc)
            return pd.DataFrame()

        try:
            result = data["chart"]["result"][0]
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("MOVE: unexpected JSON structure: %s", exc)
            return pd.DataFrame()

        df = self._parse(result)
        if df.empty:
            logger.warning("MOVE: no rows parsed")
            return df

        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        upsert_csv(df, out_path, date_col="date")
        logger.info("MOVE: saved %d rows -> %s", len(df), out_path)
        return df

    def _parse(self, result: dict) -> pd.DataFrame:
        timestamps = result.get("timestamp", [])
        q = result.get("indicators", {}).get("quote", [{}])[0]
        if not timestamps:
            return pd.DataFrame()

        def _floats(lst: list) -> list[float | None]:
            return [float(v) if v is not None else None for v in lst]

        n = len(timestamps)
        df = pd.DataFrame({
            "date":  [_epoch_to_date(t) for t in timestamps],
            "open":  _floats(q.get("open",  [None] * n)),
            "high":  _floats(q.get("high",  [None] * n)),
            "low":   _floats(q.get("low",   [None] * n)),
            "close": _floats(q.get("close", [None] * n)),
        })
        df = df.dropna(subset=["close"])
        return df[df["date"] >= self._start].sort_values("date").reset_index(drop=True)
