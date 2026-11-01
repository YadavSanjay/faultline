"""Portfolio-construction / position-sizing reads for the UI (V1 stored-only).

The sizing runner writes one report per run to
``data/pricer/sizing/<date>/sizing_<run_id>.json`` plus a stable ``sizing_latest.json``
pointer. Each report has the three risk-appetite profiles (balanced / aggressive / risky)
with per-trade target sizes, risk contributions and VaR budgets, plus a ``revaluation``
block giving PV / Risk / Capital for each configuration (and the current book). The BFF
serves the latest report for a date and a per-profile slice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .settings import data_dir, project_root


def _sizing_dir() -> Path:
    return data_dir() / "pricer" / "sizing"


def available_dates() -> list[str]:
    base = _sizing_dir()
    if not base.exists():
        return []
    dates = [d.name for d in base.iterdir() if d.is_dir() and any(d.glob("sizing_*.json"))]
    return sorted(dates, reverse=True)


def _load(valuation_date: str | None) -> tuple[str | None, dict[str, Any] | None]:
    dates = available_dates()
    if not dates:
        return None, None
    vdate = valuation_date if valuation_date in dates else dates[0]
    d = _sizing_dir() / vdate
    latest = d / "sizing_latest.json"
    if latest.exists():
        path: Path | None = latest
    else:
        runs = sorted(d.glob("sizing_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        path = runs[0] if runs else None
    if path is None:
        return vdate, None
    try:
        return vdate, json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return vdate, None


def report(valuation_date: str | None) -> dict[str, Any]:
    """Full sizing report: three profiles + re-valuation, for the overview + comparison."""
    vdate, rep = _load(valuation_date)
    if rep is None:
        return {"available": False, "valuation_date": vdate,
                "available_dates": available_dates(), "profiles": {}}
    rep["available"] = True
    rep["valuation_date"] = vdate
    rep["available_dates"] = available_dates()
    return rep


def profile_detail(profile: str, valuation_date: str | None) -> dict[str, Any]:
    """One profile (balanced | aggressive | risky): metrics, per-trade sizes, re-valuation."""
    vdate, rep = _load(valuation_date)
    prof = (rep or {}).get("profiles", {}).get(profile) if rep else None
    if prof is None:
        return {"available": False, "profile": profile, "valuation_date": vdate,
                "available_profiles": list((rep or {}).get("profiles", {}).keys()) if rep else []}
    reval = (rep or {}).get("revaluation", {}).get(profile)
    return {
        "available": True,
        "valuation_date": vdate,
        "run_id": (rep or {}).get("run_id"),
        "git_sha": (rep or {}).get("git_sha"),
        "capital_base": (rep or {}).get("capital_base"),
        "expected_returns": (rep or {}).get("expected_returns", {}),
        "revaluation": reval,
        **prof,
    }


def rebalance(profile: str, valuation_date: str | None) -> dict[str, Any]:
    """Rebalancing blotter for a profile: each position framed as an executable trade —
    current → target notional, signed delta, % change and direction — sorted by trade size.
    Read-only: this is the sizing engine's target book expressed as trades to execute, not a
    write action. All fields come from the stored sizing report (no engine call)."""
    vdate, rep = _load(valuation_date)
    prof = (rep or {}).get("profiles", {}).get(profile) if rep else None
    if prof is None:
        return {"available": False, "profile": profile, "valuation_date": vdate,
                "available_profiles": list((rep or {}).get("profiles", {}).keys()) if rep else []}

    rows: list[dict[str, Any]] = []
    n_inc = n_dec = n_hold = 0
    gross_turnover = 0.0
    for s in prof.get("sizes", []):
        cur = float(s.get("current_notional") or 0.0)
        tgt = float(s.get("target_notional") or 0.0)
        delta = tgt - cur
        gross_turnover += abs(delta)
        direction = "increase" if delta > 1e-6 else "decrease" if delta < -1e-6 else "hold"
        n_inc += direction == "increase"
        n_dec += direction == "decrease"
        n_hold += direction == "hold"
        rows.append({
            "trade_id": s.get("trade_id"),
            "description": s.get("description", ""),
            "asset_class": s.get("asset_class", ""),
            "is_hedge": bool(s.get("is_hedge")),
            "multiplier": s.get("multiplier"),
            "current_notional": cur,
            "target_notional": tgt,
            "delta_notional": delta,
            "pct_change": (delta / cur) if cur else None,
            "direction": direction,
            "risk_contribution": s.get("risk_contribution"),
            "risk_share": s.get("risk_share"),
            "standalone_var99": s.get("standalone_var99"),
        })
    rows.sort(key=lambda r: abs(r["delta_notional"]), reverse=True)

    return {
        "available": True,
        "profile": profile,
        "label": prof.get("label", profile),
        "valuation_date": vdate,
        "run_id": (rep or {}).get("run_id"),
        "capital_base": (rep or {}).get("capital_base"),
        "binding_constraint": prof.get("binding_constraint"),
        "var_budget": prof.get("var_budget"),
        "achieved_var99": prof.get("achieved_var99"),
        "gross_leverage": prof.get("gross_leverage"),
        "gross_turnover": gross_turnover,
        "n_increase": n_inc,
        "n_decrease": n_dec,
        "n_hold": n_hold,
        "n_trades": len(rows),
        "trades": rows,
    }


def book_variant(profile: str) -> dict[str, Any]:
    """The generated book variant for a profile (``portfolio_<profile>.json``) for preview +
    download. The file is produced by ``src/pricer/sizing/book_variants.py`` (base book x the
    profile's multipliers); this only reads it — no write / apply."""
    path = project_root() / "src" / "pricer" / "portfolio" / f"portfolio_{profile}.json"
    if not path.exists():
        return {"available": False, "profile": profile,
                "reason": f"no book variant for '{profile}' — run the sizing runner to generate it"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"available": False, "profile": profile, "reason": f"could not read variant: {exc}"}
    return {
        "available": True,
        "profile": profile,
        "filename": f"portfolio_{profile}.json",
        "name": raw.get("name", ""),
        "description": raw.get("description", ""),
        "sizing_basis": raw.get("sizing_basis", {}),
        "n_trades": len(raw.get("trades", []) or []),
        "book": raw,
    }


def _portfolio_dir() -> Path:
    return project_root() / "src" / "pricer" / "portfolio"


def active_book() -> dict[str, Any]:
    """Identity of the currently active book (``portfolio.json`` every runner prices unless
    overridden) — so the UI can show which variant is live."""
    path = _portfolio_dir() / "portfolio.json"
    if not path.exists():
        return {"available": False, "reason": "no active portfolio.json"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"available": False, "reason": f"could not read portfolio.json: {exc}"}
    basis = raw.get("sizing_basis") or {}
    return {
        "available": True,
        "name": raw.get("name", ""),
        "profile": basis.get("profile"),        # None for the base / unsized book
        "label": basis.get("label", ""),
        "sizing_run_id": basis.get("sizing_run_id"),
        "n_trades": len(raw.get("trades", []) or []),
    }


def activate_variant(profile: str) -> dict[str, Any]:
    """Gated write: promote a sized book variant (``portfolio_<profile>.json``) to the active
    book (``portfolio.json``) that every runner prices next. Atomic, verbatim copy. Returns the
    previous + new active profile for the audit record."""
    src = _portfolio_dir() / f"portfolio_{profile}.json"
    if not src.exists():
        return {"ok": False, "error": f"no book variant for '{profile}' to activate"}
    try:
        content = src.read_text(encoding="utf-8")
        book = json.loads(content)             # validate before we overwrite the active book
    except Exception as exc:
        return {"ok": False, "error": f"variant unreadable: {exc}"}

    prev = active_book()
    dst = _portfolio_dir() / "portfolio.json"
    tmp = dst.with_name(f".{dst.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(dst)
    return {
        "ok": True,
        "profile": profile,
        "previous_profile": prev.get("profile") if prev.get("available") else None,
        "name": book.get("name", ""),
        "n_trades": len(book.get("trades", []) or []),
    }
