"""
Yahoo Finance Options — fallback chain downloader.

Used when CBOE delayed JSON is unavailable. Not used for SPX (Yahoo does not
reliably serve SPX chains through the v7 endpoint).

Two-step protocol per underlying:
  Step 1 — discover expiries:
    GET /v7/finance/options/{TICKER}
    → response.optionChain.result[0].expirationDates = [epoch, ...]

  Step 2 — fetch chain per expiry:
    GET /v7/finance/options/{TICKER}?date={epoch}
    → response.optionChain.result[0].options[0].calls[]/puts[]
      Fields: strike, bid, ask, lastPrice, impliedVolatility, openInterest,
              volume, delta, gamma, percentChange, contractSymbol

Auth: browser User-Agent required.

Output: data/marketdata/options/chains_yahoo/{DISPLAY}_yahoo_chains.csv
  snapshot_date, underlying, expiry_date, days_to_expiry, put_call, strike,
  bid, ask, mid, last_price, open_interest, volume, iv, contract_symbol
"""

import logging
import time
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import requests

from ..common.data_store import get_output_path, upsert_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)

_CRUMB_URL   = "https://query2.finance.yahoo.com/v1/test/csrfToken"
_CONSENT_URL = "https://finance.yahoo.com"


def _epoch_to_date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d")


class YahooOptionsDownloader:
    """
    Download option chains from Yahoo Finance as a fallback to CBOE.

    Yahoo Finance v7/finance/options now requires a session crumb.
    We fetch the crumb once per session via the CSRF token endpoint,
    then include it as a ?crumb= query parameter in all subsequent calls.
    """

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["yahoo_options"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._client = HttpClient(src_cfg, credentials)
        self._ep_base   = src_cfg["endpoint_base"]
        self._ep_expiry = src_cfg["endpoint_expiry"]
        self._out_dir   = src_cfg["output_dir"]
        self._file_tpl  = src_cfg["output_file_template"]
        self._crumb: str = ""

        # Only single-stock and ETF underlyings (skip SPX)
        seen: set[str] = set()
        self._underlyings: list[dict] = []
        for inst in config["portfolio"]:
            if inst.get("yahoo") is None:
                continue
            key = inst["yahoo"]
            if key not in seen:
                seen.add(key)
                self._underlyings.append(inst)

    def _fetch_crumb(self) -> bool:
        """
        Fetch Yahoo Finance session crumb (required since ~2024).
        Process: visit finance.yahoo.com to get cookies, then hit crumb endpoint.
        Returns True if crumb was obtained successfully.
        """
        ua = self._cfg.get("extra_headers", {}).get(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        headers = {"User-Agent": ua, "Accept": "application/json"}
        session = requests.Session()
        session.headers.update(headers)

        # Warm up cookies
        try:
            session.get(_CONSENT_URL, timeout=15)
            time.sleep(0.5)
        except Exception:
            pass

        # Fetch crumb
        try:
            resp = session.get(_CRUMB_URL, timeout=15)
            if resp.ok:
                data = resp.json()
                self._crumb = data.get("crumb", "")
                if self._crumb:
                    # Inject cookies and crumb into our HttpClient session
                    self._client._session.cookies.update(session.cookies)
                    logger.info("Yahoo crumb obtained: %s...", self._crumb[:8])
                    return True
        except Exception as exc:
            logger.warning("Yahoo crumb fetch failed: %s", exc)

        logger.warning(
            "Yahoo options: could not obtain crumb — chains will return 401. "
            "CBOE chains (cboe_chains source) remain the primary and are unaffected."
        )
        return False

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> dict[str, pd.DataFrame]:
        snap_date = date.today()
        logger.info("Yahoo options: snapshot %s, %d underlyings (SPX excluded)",
                    snap_date, len(self._underlyings))
        if not self._fetch_crumb():
            logger.warning("Yahoo options: skipping all underlyings (no crumb)")
            return {}
        results: dict[str, pd.DataFrame] = {}

        for inst in self._underlyings:
            yahoo  = inst["yahoo"]
            display = inst["display"]
            df = self._fetch_all_expiries(yahoo, display, snap_date)
            results[display] = df
            if not df.empty:
                out_path = get_output_path(
                    self._asset_class,
                    f"{self._out_dir}/{self._file_tpl.format(display=display)}",
                )
                upsert_csv(df, out_path,
                           date_col="snapshot_date",
                           key_cols=["snapshot_date", "contract_symbol"])
                logger.info("Yahoo options %s: %d contracts -> %s",
                            display, len(df), out_path)

        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_all_expiries(
        self, yahoo_sym: str, display: str, snap_date: date
    ) -> pd.DataFrame:
        # Step 1: get expiry list (include crumb in params)
        endpoint = self._ep_base.format(yahoo_ticker=yahoo_sym)
        crumb_params = {"crumb": self._crumb} if self._crumb else {}
        try:
            resp = self._client.get(endpoint, params=crumb_params)
            data = resp.json()
        except Exception as exc:
            logger.warning("Yahoo options %s (expiry list): %s", display, exc)
            return pd.DataFrame()

        try:
            result0 = data["optionChain"]["result"][0]
            expiry_epochs: list[int] = result0.get("expirationDates", [])
        except (KeyError, IndexError):
            logger.warning("Yahoo options %s: unexpected response structure", display)
            return pd.DataFrame()

        if not expiry_epochs:
            logger.warning("Yahoo options %s: no expiry dates found", display)
            return pd.DataFrame()

        logger.info("Yahoo options %s: %d expiries found", display, len(expiry_epochs))

        # Step 2: fetch chain per expiry
        all_frames: list[pd.DataFrame] = []
        for epoch in expiry_epochs:
            expiry_str = _epoch_to_date(epoch)
            dte = (date.fromisoformat(expiry_str) - snap_date).days
            if dte < 0:
                continue  # skip expired

            ep = self._ep_expiry.format(yahoo_ticker=yahoo_sym, expiry_epoch=epoch)
            try:
                resp = self._client.get(ep, params=crumb_params)
                data = resp.json()
                result = data["optionChain"]["result"][0]
                opts_block = result["options"][0]
            except Exception as exc:
                logger.warning("Yahoo options %s expiry %s: %s", display, expiry_str, exc)
                continue

            for side, raw_list in (("call", opts_block.get("calls", [])),
                                   ("put",  opts_block.get("puts", []))):
                for opt in raw_list:
                    bid = _flt(opt.get("bid"))
                    ask = _flt(opt.get("ask"))
                    mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
                    all_frames.append({
                        "snapshot_date":  snap_date.isoformat(),
                        "underlying":     display,
                        "expiry_date":    expiry_str,
                        "days_to_expiry": dte,
                        "put_call":       side,
                        "strike":         _flt(opt.get("strike")),
                        "bid":            bid,
                        "ask":            ask,
                        "mid":            mid,
                        "last_price":     _flt(opt.get("lastPrice")),
                        "open_interest":  _int(opt.get("openInterest")),
                        "volume":         _int(opt.get("volume")),
                        "iv":             _flt(opt.get("impliedVolatility")),
                        "contract_symbol": opt.get("contractSymbol", ""),
                    })

        if not all_frames:
            return pd.DataFrame()

        df = pd.DataFrame(all_frames)
        df = df.sort_values(["expiry_date", "put_call", "strike"]).reset_index(drop=True)
        logger.info("Yahoo options %s: %d contracts, %d expiries",
                    display, len(df), df["expiry_date"].nunique())
        return df


def _flt(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
