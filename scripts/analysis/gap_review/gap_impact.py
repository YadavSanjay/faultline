"""Dollar impact of the measured curve gap per trade, for the institutional gap review.

Combines the outputs of ``curve_sensitivities.py`` (key-rate DV01s) and ``curve_vs_market.py``
(market minus our par rate per tenor): impact = Σ key-rate DV01 × (market − ours) in bp, i.e. the
PV change if our curve were moved onto the market. Tenors without enough market trades contribute
nothing; a node between two measured tenors uses their average gap. First-order (linear) only, and
before any offset through the bonds' credit spreads. Run both scripts first, then:

    conda run --no-capture-output -n py312 python scripts/analysis/gap_review/gap_impact.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common import storage
from src.pricer.portfolio.portfolio_loader import load_portfolio_config

NODE_ORDER = ["1M", "3M", "6M", "9M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "12Y", "15Y", "20Y", "30Y"]


def main() -> None:
    """Reads both JSON outputs, prints per-trade impact and writes ``gap_impact.json``."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--valuation-date", default=None, help="ISO date; default: the book's default")
    p.add_argument("--out", default=None, help="folder holding both inputs (default: data/analysis/gap_review/<date>)")
    p.add_argument("--min-impact", type=float, default=1000.0, help="only print trades moving more than this ($)")
    a = p.parse_args()
    vdate = (date.fromisoformat(a.valuation_date) if a.valuation_date
             else load_portfolio_config().default_valuation_date)
    out_dir = str(a.out or storage.data_path("analysis", "gap_review", vdate.isoformat()))

    sens = json.loads(storage.read_text(storage.join(out_dir, "curve_sensitivities.json")))
    market = json.loads(storage.read_text(storage.join(out_dir, "curve_vs_market.json")))
    gap = {r["tenor"]: -r["ours_minus_market_bp"] for r in market["tenors"]}     # market - ours, bp
    # Fill unmeasured nodes that sit between two measured ones with the average of their neighbours.
    measured = [n for n in NODE_ORDER if n in gap]
    for n in NODE_ORDER:
        if n not in gap:
            i = NODE_ORDER.index(n)
            lo = next((m for m in reversed(NODE_ORDER[:i]) if m in measured), None)
            hi = next((m for m in NODE_ORDER[i + 1:] if m in measured), None)
            if lo and hi:
                gap[n] = (gap[lo] + gap[hi]) / 2

    impact = {}
    for tid, kr in sens["key_rate_dv01"].items():
        impact[tid] = {"total": sum(kr.get(n, 0.0) * g for n, g in gap.items()),
                       "by_node": {n: kr.get(n, 0.0) * g for n, g in gap.items() if abs(kr.get(n, 0.0) * g) >= 1}}
    book = sum(v["total"] for v in impact.values())

    print("gap (market - ours, bp):", {n: round(g, 2) for n, g in gap.items()})
    for tid, v in sorted(impact.items(), key=lambda kv: -abs(kv[1]["total"])):
        if abs(v["total"]) >= a.min_impact:
            pv = sens["base"].get(tid, {}).get("pv")
            print(f"{tid:9s} pv {pv:>16,.0f}   impact {v['total']:>12,.0f}")
    print(f"book impact {book:,.0f}")

    path = storage.join(out_dir, "gap_impact.json")
    storage.write_text_atomic(path, json.dumps({"valuation_date": vdate.isoformat(), "gap_bp": gap,
                                                "book_impact": book, "trades": impact}, indent=1))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
