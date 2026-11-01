"""Option-implied signal reads for the UI (P6 / B6) — a read layer over the P1 signal files.

The marketdata ``signals_runner`` derives, per underlier, the option-implied signals that scenariogen
consumes (design workstream B): per-name ATM IV + 30d/90d term structure, 25-delta skew, variance-risk
premium, positioning, an expected move, a Breeden-Litzenberger risk-neutral density with tail
probabilities, and an option-implied credit spread (Merton + CreditGrades). This module surfaces them
for the UI so a desk can see *what the option market is pricing* alongside the book:

    option_signals.csv   one row per (underlier, snapshot): IV/term/skew/VRP/positioning + tail probs
    rnd_curves.csv       long RND grid (underlier, snapshot, strike, pdf, cdf)
    credit_implied.csv   Merton distance-to-default / PD + CreditGrades spread, with a CDS cross-check

Read-only, direct from the files under ``data/marketdata/options/signals/`` (library-only, no service),
mirroring ``marketdata.py``. Everything degrades to an empty payload when the signals aren't on disk.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import data_dir

# Latest-snapshot summary fields for the name catalog (the full row is served in the detail view).
_SUMMARY_COLS = [
    "atm_iv_30d", "atm_iv_90d", "term_ratio", "term_inverted", "term_slope",
    "skew_25d_30d", "vrp_20d", "realized_vol_20d", "pc_oi_ratio", "oi_top5_share",
    "expected_move_front", "expected_move_front_dte",
    "prob_down_10", "prob_down_20", "prob_down_30", "prob_up_10", "prob_up_20",
    "rnd_spot", "rnd_forward", "rnd_dte", "n_contracts", "n_expiries",
]
# Per-snapshot history series (only 2 snapshots today, but the shape supports more).
_HISTORY_COLS = ["atm_iv_30d", "atm_iv_90d", "term_ratio", "skew_25d_30d", "vrp_20d",
                 "realized_vol_20d", "expected_move_front"]


def _signals_dir() -> Path:
    return data_dir() / "marketdata" / "options" / "signals"


def _read(name: str) -> pd.DataFrame:
    path = _signals_dir() / name
    # key on mtime so a regenerated signals file busts the cache (cf. marketdata._csv_stats_cached)
    mtime = path.stat().st_mtime if path.exists() else 0.0
    return _read_cached(path.as_posix(), mtime)


@lru_cache(maxsize=16)
def _read_cached(path_str: str, _mtime: float) -> pd.DataFrame:
    path = Path(path_str)
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    if "snapshot_date" in df.columns:
        df["snapshot_date"] = df["snapshot_date"].astype(str)
    return df


def _latest_per_name(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "underlying" not in df.columns:
        return df
    return df.sort_values("snapshot_date").groupby("underlying", as_index=False).tail(1)


def _clean(v: Any) -> Any:
    """JSON-safe scalar: NaN/inf -> None, numpy -> python."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(df.to_json(orient="records", date_format="iso"))


# --- public API ------------------------------------------------------------ #
def list_signals() -> dict[str, Any]:
    """Name catalog: the latest option-implied signal summary per underlier + availability flags."""
    sig = _read("option_signals.csv")
    if sig.empty:
        return {"names": [], "snapshots": [], "count": 0,
                "note": "No option signals on disk — run `python -m src.marketdata.options.signals_runner`."}
    rnd_names = set(_read("rnd_curves.csv").get("underlying", pd.Series(dtype=str)).unique())
    credit = _latest_per_name(_read("credit_implied.csv"))
    credit_by = {str(r["underlying"]): r for _, r in credit.iterrows()} if not credit.empty else {}

    latest = _latest_per_name(sig).sort_values("underlying")
    names: list[dict[str, Any]] = []
    for _, row in latest.iterrows():
        name = str(row["underlying"])
        entry: dict[str, Any] = {"underlying": name, "snapshot_date": str(row["snapshot_date"]),
                                 "has_rnd": name in rnd_names, "has_credit": name in credit_by}
        for c in _SUMMARY_COLS:
            if c in row:
                entry[c] = _clean(row[c])
        cr = credit_by.get(name)
        entry["merton_dd"] = _clean(cr["merton_dd"]) if cr is not None else None
        names.append(entry)
    return {"names": names, "snapshots": sorted(sig["snapshot_date"].unique().tolist()),
            "count": len(names)}


def signal_detail(name: str) -> dict[str, Any] | None:
    """Full latest signal row for one underlier + a short per-snapshot history + implied credit."""
    sig = _read("option_signals.csv")
    if sig.empty:
        return None
    sub = sig[sig["underlying"].astype(str) == name].sort_values("snapshot_date")
    if sub.empty:
        return None
    latest = {k: _clean(v) for k, v in sub.iloc[-1].to_dict().items()}
    history = [
        {"snapshot_date": str(r["snapshot_date"]),
         **{c: _clean(r[c]) for c in _HISTORY_COLS if c in sub.columns}}
        for _, r in sub.iterrows()
    ]
    credit = _read("credit_implied.csv")
    cred_row = None
    if not credit.empty:
        cr = credit[credit["underlying"].astype(str) == name].sort_values("snapshot_date")
        if not cr.empty:
            cred_row = {k: _clean(v) for k, v in cr.iloc[-1].to_dict().items()}
    return {"underlying": name, "latest": latest, "history": history,
            "credit_implied": cred_row, "has_rnd": name in set(
                _read("rnd_curves.csv").get("underlying", pd.Series(dtype=str)).astype(str).unique())}


def rnd_curve(name: str) -> dict[str, Any] | None:
    """Latest risk-neutral density for one underlier (strike grid + pdf + cdf) for charting."""
    rnd = _read("rnd_curves.csv")
    if rnd.empty:
        return None
    sub = rnd[rnd["underlying"].astype(str) == name]
    if sub.empty:
        return None
    last = sub["snapshot_date"].max()
    curve = sub[sub["snapshot_date"] == last].sort_values("strike")
    # spot/forward/tail probs come from the signals row for the same name/snapshot
    sig = _read("option_signals.csv")
    meta: dict[str, Any] = {}
    if not sig.empty:
        s = sig[(sig["underlying"].astype(str) == name) & (sig["snapshot_date"] == str(last))]
        if not s.empty:
            r = s.iloc[-1]
            meta = {k: _clean(r[k]) for k in
                    ("rnd_spot", "rnd_forward", "rnd_dte", "expected_move_front",
                     "prob_down_10", "prob_down_20", "prob_down_30", "prob_up_10", "prob_up_20")
                    if k in r}
    return {"underlying": name, "snapshot_date": str(last),
            "strike": [_clean(v) for v in curve["strike"].tolist()],
            "pdf": [_clean(v) for v in curve["pdf"].tolist()],
            "cdf": [_clean(v) for v in curve["cdf"].tolist()],
            **meta}


def credit_table() -> dict[str, Any]:
    """Option-implied credit across names, ranked by distance-to-default (riskiest first).

    The CreditGrades spread is compressed by the synthetic-equity leverage (documented B3 caveat),
    so the robust signal is the DD *ranking* — surfaced here with the CDS cross-check where present.
    """
    credit = _latest_per_name(_read("credit_implied.csv"))
    if credit.empty:
        return {"rows": [], "count": 0, "note": "No option-implied credit on disk (B3)."}
    keep = ["underlying", "snapshot_date", "merton_dd", "merton_pd", "cg_spread_bp",
            "merton_spread_bp", "cds_reference_bp", "basis_bp", "leverage", "equity_vol"]
    cols = [c for c in keep if c in credit.columns]
    rows = credit[cols].sort_values("merton_dd", na_position="last")
    return {"rows": [{k: _clean(v) for k, v in r.items()} for r in _records(rows)],
            "columns": cols, "count": len(rows)}
