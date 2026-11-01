"""News & events reads for the UI (P6/A5) — a read layer over the P3 news feeds.

Surfaces what the marketdata news runner ingests (design workstream A): the SEC Form 8-K item-code
event stream (``sec_events.csv``) and GDELT article-volume/tone salience (``gdelt_salience.csv``),
plus the *resolved* scenariogen calibration — the clamped per-name multiplier on the ARag
idiosyncratic leg, the 5.02 key-man live triggers, and the 2.02 earnings calendar — computed
point-in-time by reusing ``NewsEventProvider`` (so the UI shows exactly what a scenario run would).

Read-only, direct from ``data/marketdata/news/`` (library-only, no service), mirroring
``marketdata.py`` / ``options_signals.py``. Everything degrades to an empty payload when the feeds
aren't on disk. Tier-3 display headlines (Yahoo/Nasdaq RSS) are intentionally absent — dropped at
the §8 ToS decision gate; Tiers 1-2 (8-K + GDELT) are what ship.
"""

from __future__ import annotations

import datetime as _dt
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from src.scenariogen.config import load_news_config
from src.scenariogen.news.gate import NewsGateConfig
from src.scenariogen.news.mapping import EventRow, NewsMapConfig
from src.scenariogen.news.provider import NewsEventProvider

from .settings import data_dir, project_root

# SEC Form 8-K item-code labels (design §2.2) — for decoding the event stream in the UI.
_ITEM_LABELS: dict[str, str] = {
    "2.02": "Results of operations (earnings)",
    "5.02": "Departure of directors / officers",
    "4.02": "Non-reliance on prior financials (restatement)",
    "2.06": "Material impairment",
    "1.01": "Entry into a material agreement",
    "1.02": "Termination of a material agreement",
    "1.05": "Material cybersecurity incident",
    "2.03": "New material debt obligation",
    "2.04": "Debt triggering event",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}


def _news_dir() -> Path:
    return data_dir() / "marketdata" / "news"


def _read(name: str) -> pd.DataFrame:
    path = _news_dir() / name
    mtime = path.stat().st_mtime if path.exists() else 0.0
    return _read_cached(path.as_posix(), mtime)


@lru_cache(maxsize=8)
def _read_cached(path_str: str, _mtime: float) -> pd.DataFrame:
    path = Path(path_str)
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception:
        return pd.DataFrame()
    return df


def _default_as_of() -> _dt.date:
    """Default the calibration to today (the live view); the UI can pass ``as_of`` for PIT."""
    return _dt.date.today()


def _provider() -> tuple[NewsEventProvider | None, dict[str, Any]]:
    """Build a NewsEventProvider from the on-disk feeds (via data_dir, dual-mode safe)."""
    try:
        cfg = load_news_config(project_root())
    except FileNotFoundError:
        cfg = {}                                    # config absent -> mapping/gate use their defaults
    sec = _read("sec_events.csv")
    if sec.empty or "ticker" not in sec.columns:
        return None, cfg
    events: dict[str, list[EventRow]] = {}
    for _, r in sec.iterrows():
        events.setdefault(str(r["ticker"]).upper(), []).append(EventRow(
            filing_date=str(r["filing_date"]), item_codes=str(r.get("item_codes", "")),
            accession=str(r.get("accession", "")), weight=float(r.get("weight", 0.0) or 0.0)))
    tone: dict[str, list[tuple[str, float]]] = {}
    gd = _read("gdelt_salience.csv")
    if not gd.empty and {"ticker", "date", "tone_z_63d"}.issubset(gd.columns):
        gd = gd.sort_values("date")
        for _, r in gd.iterrows():
            z = r.get("tone_z_63d")
            if z not in (None, "", "nan"):
                try:
                    tone.setdefault(str(r["ticker"]).upper(), []).append((str(r["date"]), float(z)))
                except (TypeError, ValueError):
                    continue
    prov = NewsEventProvider(events, tone, NewsMapConfig.from_dict(cfg), NewsGateConfig.from_dict(cfg),
                             list(cfg.get("keyman_items", ["5.02"])),
                             list(cfg.get("earnings_items", ["2.02"])))
    return prov, cfg


def _decorate_event(row: dict[str, Any]) -> dict[str, Any]:
    codes = [c for c in str(row.get("item_codes", "")).split(",") if c]
    row["items"] = [{"code": c, "label": _ITEM_LABELS.get(c, c)} for c in codes]
    return row


# --- public API ------------------------------------------------------------ #
def list_events(ticker: str | None = None, limit: int = 200) -> dict[str, Any]:
    """The 8-K event stream (newest first), item codes decoded, optionally filtered to one name."""
    sec = _read("sec_events.csv")
    if sec.empty:
        return {"events": [], "tickers": [], "item_labels": _ITEM_LABELS, "count": 0,
                "note": "No 8-K events on disk — run `python -m src.marketdata.news.runner`."}
    df = sec.copy()
    if ticker:
        df = df[df["ticker"].astype(str).str.upper() == ticker.upper()]
    df = df.sort_values("filing_date", ascending=False).head(max(1, min(int(limit), 1000)))
    if "weight" in df.columns:                      # read as str; the UI contract is a number (bad -> null)
        df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    events = [_decorate_event(r) for r in json.loads(df.to_json(orient="records"))]
    return {"events": events, "tickers": sorted(sec["ticker"].astype(str).str.upper().unique().tolist()),
            "item_labels": _ITEM_LABELS, "count": len(events),
            "total": len(sec), "date_max": str(sec["filing_date"].max())}


def calibration(as_of: str | None = None) -> dict[str, Any]:
    """The resolved scenariogen news calibration point-in-time: gate, per-name multiplier + legs,
    the 5.02 key-man live triggers, and the 2.02 earnings calendar (what a scenario run would use)."""
    prov, _cfg = _provider()
    day = _parse_date(as_of) or _default_as_of()
    if prov is None:
        return {"as_of": day.isoformat(), "applied": False, "legs": {}, "live_triggers": {},
                "earnings": {}, "note": "No 8-K events on disk."}
    res = prov.resolve(day)
    out = res.to_dict()
    # decorate the key-man triggers with their item label
    out["live_trigger_labels"] = {code: _ITEM_LABELS.get(code, code) for code in res.live_triggers}
    return out


def name_detail(ticker: str, as_of: str | None = None) -> dict[str, Any] | None:
    """Per-name news: its 8-K events (decoded), GDELT tone/volume series, and the resolved leg."""
    tkr = ticker.upper()
    sec = _read("sec_events.csv")
    if sec.empty:
        return None
    ev = sec[sec["ticker"].astype(str).str.upper() == tkr].sort_values("filing_date", ascending=False)
    if ev.empty:
        return None
    events = [_decorate_event(r) for r in json.loads(ev.to_json(orient="records"))]

    salience: list[dict[str, Any]] = []
    gd = _read("gdelt_salience.csv")
    if not gd.empty and "ticker" in gd.columns:
        sub = gd[gd["ticker"].astype(str).str.upper() == tkr].sort_values("date")
        salience = json.loads(sub.to_json(orient="records"))

    prov, _cfg = _provider()
    day = _parse_date(as_of) or _default_as_of()
    leg = None
    gate = None
    if prov is not None:
        res = prov.resolve(day)
        leg = res.leg_for(tkr)
        gate = res.gate.to_dict()
        leg = leg.to_dict() if leg else None
    return {"ticker": tkr, "as_of": day.isoformat(), "events": events, "salience": salience,
            "leg": leg, "gate": gate}


def _parse_date(s: str | None) -> _dt.date | None:
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None
