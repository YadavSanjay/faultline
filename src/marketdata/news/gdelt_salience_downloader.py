"""GDELT 2.0 article-volume / tone salience (A2, market-intelligence P3).

Tier-2 *intensity* over the 8-K event spine (design §2.3): per portfolio name, the daily GDELT
article-volume intensity and average tone, with a historical series (``timelinevolinfo`` /
``timelinetone``) so vol/tone z-scores are available immediately — no accumulation wait. The
z-scored tone feeds the scenariogen news multiplier (``β_tone·(-tone_z)``); salience != severity, so
tone is signed and the multiplier is clamped both ways.

Graceful, not fail-loud: GDELT is the intensity modulator, not the spine, and it throttles requests
(one per 5s, returning a plain-text notice at HTTP 200). A name that fails to fetch or parse simply
contributes no salience rows. Output ``gdelt_salience.csv``:
``ticker, date, article_volume, avg_tone, vol_z_63d, tone_z_63d``.
"""

from __future__ import annotations

import logging

import pandas as pd

from ..common.data_store import get_output_path, save_csv
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class GdeltSalienceDownloader:
    """Fetch per-name GDELT volume + tone timelines and z-score them (A2)."""

    def __init__(self, config: dict, credentials: dict[str, str], *, client: HttpClient | None = None):
        src_cfg = config["sources"]["gdelt"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._queries: dict[str, str] = {str(k).upper(): str(v)
                                         for k, v in config.get("gdelt_queries", {}).items()}
        self._client = client if client is not None else HttpClient(src_cfg, credentials)
        self._vol_tpl = src_cfg["vol_endpoint_template"]
        self._tone_tpl = src_cfg["tone_endpoint_template"]
        self._timespan = str(src_cfg.get("timespan", "6m"))
        self._z_window = int(src_cfg.get("z_window_days", 63))

    def run(self) -> pd.DataFrame:
        logger.info("GDELT: fetching salience for %d name(s)", len(self._queries))
        frames = [df for df in (self._fetch_name(t, q) for t, q in self._queries.items()) if not df.empty]
        if not frames:
            logger.warning("GDELT: no salience retrieved for any name (throttled / no coverage) — "
                           "writing nothing; news runs on the 8-K spine alone")
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True).sort_values(["ticker", "date"]).reset_index(drop=True)
        out_path = get_output_path(self._asset_class, self._cfg["output_file"])
        save_csv(combined, out_path)
        logger.info("GDELT: saved %d salience row(s) across %d name(s) -> %s",
                    len(combined), combined["ticker"].nunique(), out_path)
        return combined

    def _fetch_name(self, ticker: str, query: str) -> pd.DataFrame:
        vol = self._timeline(self._vol_tpl, query)
        tone = self._timeline(self._tone_tpl, query)
        if vol is None and tone is None:
            logger.warning("GDELT %s: no timeline data", ticker)
            return pd.DataFrame()
        df = pd.DataFrame({"date": sorted(set((vol or {}).keys()) | set((tone or {}).keys()))})
        df["article_volume"] = df["date"].map(vol or {})
        df["avg_tone"] = df["date"].map(tone or {})
        df["vol_z_63d"] = self._zscore(df["article_volume"])
        df["tone_z_63d"] = self._zscore(df["avg_tone"])
        df.insert(0, "ticker", ticker)
        return df

    def _timeline(self, tpl: str, query: str) -> dict[str, float] | None:
        """A ``date(YYYY-MM-DD) -> value`` map from one GDELT timeline endpoint, or None on failure."""
        endpoint = tpl.replace("{query}", query.replace(" ", "+")).replace("{timespan}", self._timespan)
        try:
            resp = self._client.get(endpoint)
            data = resp.json()
        except Exception as exc:
            logger.warning("GDELT timeline fetch/parse failed for %r: %s", query, exc)
            return None
        timeline = data.get("timeline", []) if isinstance(data, dict) else []
        if not timeline:
            return None
        out: dict[str, float] = {}
        for pt in timeline[0].get("data", []):
            d = str(pt.get("date", ""))[:8]                 # 20260528T000000Z -> 20260528
            if len(d) == 8:
                out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = float(pt.get("value", 0.0))
        return out or None

    def _zscore(self, s: pd.Series) -> pd.Series:
        """Trailing-window z-score; min_periods keeps early rows usable with a shorter window."""
        x = pd.to_numeric(s, errors="coerce")
        mean = x.rolling(self._z_window, min_periods=10).mean()
        std = x.rolling(self._z_window, min_periods=10).std(ddof=0)
        return ((x - mean) / std).where(std > 0)
