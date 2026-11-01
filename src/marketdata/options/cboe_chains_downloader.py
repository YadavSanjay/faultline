"""
CBOE Delayed Option Chains downloader.

One JSON call per underlying returns the COMPLETE option chain across ALL
expiries (~15-min delayed). No authentication required.

URL pattern:
  https://cdn.cboe.com/api/global/delayed_quotes/options/{TICKER}.json
  Indices use underscore prefix: _SPX, _NDX

Response structure:
  {
    "timestamp": "2026-06-17T...",
    "data": {
      "options": [
        {
          "option":            "NVDA260620C00800000",   # OSI symbol
          "bid":               15.20,
          "ask":               15.40,
          "last_trade_price":  15.30,
          "open_interest":     1234,
          "volume":            567,
          "delta":             0.45,
          "gamma":             0.02,
          "theta":            -0.05,
          "vega":              0.80,
          "iv":                0.65
        }, ...
      ]
    }
  }

OSI symbol format: {UNDERLYING}{YYMMDD}{C|P}{STRIKE*1000 zero-padded to 8}
  e.g. NVDA260620C00800000 → NVDA, 2026-06-20, Call, $800.00

Underlyings come from `portfolio` (the option book, #7-14) plus
`sources.cboe_chains.extra_underlyings` — portfolio names with no option trade whose chains are
downloaded for their option-implied signals (per-name IV, skew, bid-ask liquidity).

Output: data/marketdata/options/chains/{DISPLAY}_chains.csv
  snapshot_date, underlying, expiry_date, days_to_expiry, put_call, strike,
  bid, ask, mid, last_trade_price, open_interest, volume,
  delta, gamma, theta, vega, iv, osi_symbol

Deduplication key: (snapshot_date, osi_symbol)
Weekly runs append a new snapshot row per contract.
"""

import logging
import re
from datetime import date
from typing import Any

import pandas as pd

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_OSI_RE = re.compile(
    r"^(?P<root>[A-Z0-9_]+)"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<pc>[CP])"
    r"(?P<strike_raw>\d{8})$"
)

_CHAIN_COLS = [
    "snapshot_date", "underlying", "expiry_date", "days_to_expiry",
    "put_call", "strike",
    "bid", "ask", "mid", "last_trade_price",
    "open_interest", "volume",
    "delta", "gamma", "theta", "vega", "iv",
    "osi_symbol",
]


def _parse_osi(symbol: str, snap_date: date) -> dict | None:
    m = _OSI_RE.match(symbol.strip())
    if not m:
        return None
    try:
        expiry = date(
            2000 + int(m.group("yy")),
            int(m.group("mm")),
            int(m.group("dd")),
        )
        strike = int(m.group("strike_raw")) / 1000.0
        dte = (expiry - snap_date).days
        return {
            "expiry_date":    expiry.isoformat(),
            "days_to_expiry": dte,
            "put_call":       "call" if m.group("pc") == "C" else "put",
            "strike":         strike,
        }
    except (ValueError, OverflowError):
        return None


class CBOEChainsDownloader:
    """Download full option chains from CBOE delayed JSON API."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["cboe_chains"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._client = HttpClient(src_cfg, credentials)
        self._tpl = src_cfg["endpoint_template"]
        self._out_dir = src_cfg["output_dir"]
        self._file_tpl = src_cfg["output_file_template"]

        # Deduplicate underlyings (trades 12 and 13 both use _SPX). `extra_underlyings` are
        # portfolio names with no option trade, downloaded for their option-implied signals; they
        # are tagged with the signal output dir so they land OUTSIDE the pricer's chains/ glob
        # (keeps signal-only data out of the pricing snapshot fingerprint — see config note).
        self._signal_dir = src_cfg.get("signal_output_dir", "chains_signals")
        seen: set[str] = set()
        self._underlyings: list[dict] = []
        for inst in config["portfolio"]:
            key = inst["cboe"]
            if key not in seen:
                seen.add(key)
                self._underlyings.append({**inst, "_out_dir": self._out_dir})
        for inst in src_cfg.get("extra_underlyings", []):
            key = inst["cboe"]
            if key not in seen:
                seen.add(key)
                self._underlyings.append({**inst, "_out_dir": self._signal_dir})

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> dict[str, pd.DataFrame]:
        snap_date = date.today()
        logger.info("CBOE chains: snapshot date %s, %d underlyings",
                    snap_date, len(self._underlyings))
        results: dict[str, pd.DataFrame] = {}

        for inst in self._underlyings:
            cboe_ticker = inst["cboe"]
            display     = inst["display"]
            df = self._fetch_chain(cboe_ticker, display, snap_date)
            results[display] = df
            if not df.empty:
                out_dir = inst.get("_out_dir", self._out_dir)
                out_path = get_output_path(
                    self._asset_class,
                    f"{out_dir}/{self._file_tpl.format(display=display)}",
                )
                upsert_csv(df, out_path,
                           date_col="snapshot_date",
                           key_cols=["snapshot_date", "osi_symbol"])
                logger.info("CBOE chains %s: %d contracts -> %s", display, len(df), out_path)

        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_chain(self, cboe_ticker: str, display: str, snap_date: date) -> pd.DataFrame:
        endpoint = self._tpl.format(cboe_ticker=cboe_ticker)
        try:
            resp = self._client.get(endpoint)
            payload = resp.json()
        except Exception as exc:
            logger.warning("CBOE chains %s: %s", display, exc)
            return pd.DataFrame()

        options_raw = payload.get("data", {}).get("options", [])
        if not options_raw:
            logger.warning("CBOE chains %s: empty options list", display)
            return pd.DataFrame()

        snap_str = snap_date.isoformat()
        rows: list[dict] = []

        for opt in options_raw:
            symbol = opt.get("option", "")
            parsed = _parse_osi(symbol, snap_date)
            if parsed is None:
                continue

            bid = _float(opt.get("bid"))
            ask = _float(opt.get("ask"))
            mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None

            rows.append({
                "snapshot_date":   snap_str,
                "underlying":      display,
                "expiry_date":     parsed["expiry_date"],
                "days_to_expiry":  parsed["days_to_expiry"],
                "put_call":        parsed["put_call"],
                "strike":          parsed["strike"],
                "bid":             bid,
                "ask":             ask,
                "mid":             mid,
                "last_trade_price": _float(opt.get("last_trade_price")),
                "open_interest":   _int(opt.get("open_interest")),
                "volume":          _int(opt.get("volume")),
                "delta":           _float(opt.get("delta")),
                "gamma":           _float(opt.get("gamma")),
                "theta":           _float(opt.get("theta")),
                "vega":            _float(opt.get("vega")),
                "iv":              _float(opt.get("iv")),
                "osi_symbol":      symbol,
            })

        if not rows:
            logger.warning("CBOE chains %s: 0 valid rows after OSI parsing", display)
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=_CHAIN_COLS)
        # Filter to expiries ≥ today (drop expired contracts)
        df = df[df["days_to_expiry"] >= 0].sort_values(
            ["expiry_date", "put_call", "strike"]
        ).reset_index(drop=True)

        n_expiries = df["expiry_date"].nunique()
        logger.info("CBOE chains %s: %d contracts, %d expiries (DTE 0-%d)",
                    display, len(df), n_expiries, df["days_to_expiry"].max())
        return df


def _float(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
