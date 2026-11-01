"""Generate risk-appetite book variants from the base book + sizing multipliers.

The canonical **base** book (`portfolio_base.json`, the original ~$50mm placeholder sizes) is
the input; for each profile we apply that profile's per-trade multipliers to produce a sized
book `portfolio_<profile>.json` (balanced / aggressive / risky). The configured default
profile's book is also copied to `portfolio.json` — the *active default* every runner prices
unless `--book`/`PRICER_BOOK` selects another. The base file itself is never overwritten.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .optimizer import SizingResult


def _scale_int(value: float, m: float) -> int:
    return round(value * m)


def _apply_multipliers(base_raw: dict[str, Any], mult: dict[str, float]) -> dict[str, Any]:
    """Scale every position lever (notional, quantity, option-leg qty, usd_notional) by its
    per-trade multiplier. First-order PV/risk scale linearly with size."""
    book = copy.deepcopy(base_raw)
    for t in book.get("trades", []):
        m = mult.get(t["trade_id"], 1.0)
        if t.get("notional") is not None:
            t["notional"] = _scale_int(t["notional"], m)
        p = t.get("params", {})
        if "quantity" in p:
            p["quantity"] = _scale_int(p["quantity"], m)
        if "usd_notional" in p:
            p["usd_notional"] = _scale_int(p["usd_notional"], m)
        for leg in p.get("legs", []):
            if "quantity" in leg:
                leg["quantity"] = _scale_int(leg["quantity"], m)
    return book


def write_variants(base_raw: dict[str, Any], results: dict[str, SizingResult],
                   capital_base: float, run_id: str, valuation_date: str,
                   default_profile: str, out_dir: Path) -> dict[str, Path]:
    """Write `portfolio_<profile>.json` for each profile + copy the default to `portfolio.json`."""
    out_dir = Path(out_dir)
    written: dict[str, Path] = {}
    for key, res in results.items():
        mult = {s.trade_id: s.multiplier for s in res.sizes}
        book = _apply_multipliers(base_raw, mult)
        book["name"] = f"AI-Themed Portfolio ({res.label} sizing)"
        book["description"] = (
            f"30-trade AI-themed book sized to the {res.label} risk-appetite mandate "
            f"(src/pricer/sizing; docs/design/portfolio_construction_design.md), on the real ~4% "
            f"SOFR curve. Derived from portfolio_base.json (the ~$50mm placeholder book) by applying "
            f"this profile's per-trade multipliers. Select at pricing time with --book {key}."
        )
        book["sizing_basis"] = {
            "profile": key, "label": res.label, "sizing_run_id": run_id,
            "sizing_valuation_date": valuation_date, "capital_base": capital_base,
            "target_var99": res.achieved_var99, "gross_leverage": res.gross_leverage,
            "source": "src/pricer/sizing book variant (base x profile multipliers)",
        }
        path = out_dir / f"portfolio_{key}.json"
        path.write_text(json.dumps(book, indent=2) + "\n", encoding="utf-8")
        written[key] = path

    # Active default: copy the configured default profile's book to portfolio.json.
    default_path = written.get(default_profile)
    if default_path is not None:
        (out_dir / "portfolio.json").write_text(
            default_path.read_text(encoding="utf-8"), encoding="utf-8")
    return written
