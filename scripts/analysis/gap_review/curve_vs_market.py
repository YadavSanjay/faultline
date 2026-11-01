"""Our SOFR curve vs market swap rates from the DTCC public tape, for the institutional gap review.

Feeds the "Measured accuracy" part of docs/design/institutional_gap_review.md. The market rate per
tenor is the median fixed rate of new, spot-starting, standard USD SOFR OIS trades executed on the
valuation date:

* ``Action type`` NEWT, ``Event type`` TRAD, ``UPI FISN`` "NA/Swap OIS USD";
* not part of a package, not a non-standard term, no spread on either leg;
* effective 0-4 days after execution (spot-starting);
* anniversary-dated (maturity day within 3 days of the effective day) at a standard tenor;
* at least ``--min-trades`` trades for the tenor.

Needs the full DTCC rates tape for that date (``python -m src.marketdata.rates.runner --sources dtcc``;
the file keeps a rolling window). Writes ``curve_vs_market.json``. Run from the repo root:

    conda run --no-capture-output -n py312 python scripts/analysis/gap_review/curve_vs_market.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from src.common import storage
from src.marketdata.rates.sdr_records import read_sdr_records
from src.pricer.config import load_pricer_config
from src.pricer.loader.rates_adapter import RatesMarketDataAdapter
from src.pricer.portfolio.portfolio_loader import load_portfolio_config
from src.pricer.runner import _build_ctx

TENOR_MONTHS = {"1Y": 12, "2Y": 24, "3Y": 36, "5Y": 60, "7Y": 84, "10Y": 120, "12Y": 144, "15Y": 180,
                "20Y": 240, "30Y": 360}


def _not_true(s: pd.Series) -> pd.Series:
    return s.fillna("False").str.lower() != "true"


def _zero_or_blank(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0) == 0.0


def market_par_rates(vdate: date, min_trades: int) -> list[dict[str, Any]]:
    """Median market par rate (percent) per standard tenor from the DTCC tape for `vdate`."""
    day = vdate.isoformat()
    df = read_sdr_records(storage.data_path("marketdata", "rates", "dtcc_sdr_irs.csv"))
    if df.empty or "Dissemination Identifier" not in df.columns:
        raise SystemExit("dtcc_sdr_irs.csv is missing or in the legacy layout; re-ingest the DTCC rates tape")
    df = df[(df["date"] == day) & (df["Action type"] == "NEWT") & (df["Event type"] == "TRAD")
            & (df["UPI FISN"] == "NA/Swap OIS USD")
            & _not_true(df["Package indicator"]) & _not_true(df["Non-standardized term indicator"])
            & _zero_or_blank(df["Spread-Leg 1"]) & _zero_or_blank(df["Spread-Leg 2"])]
    if df.empty:
        raise SystemExit(f"no DTCC SOFR swap trades on {day}; is the date inside the ingest window?")
    executed = (pd.to_datetime(df["Execution Timestamp"], utc=True, format="ISO8601")
                .dt.tz_localize(None).dt.normalize())
    eff = pd.to_datetime(df["Effective Date"], errors="coerce")
    mat = pd.to_datetime(df["Expiration Date"], errors="coerce")
    rate = pd.to_numeric(df["Fixed rate-Leg 1"], errors="coerce").fillna(
        pd.to_numeric(df["Fixed rate-Leg 2"], errors="coerce"))
    spot = (executed.dt.strftime("%Y-%m-%d") == day) & (eff - executed).dt.days.between(0, 4)
    d = pd.DataFrame({"eff": eff, "mat": mat, "rate": rate})[spot & rate.between(0.005, 0.10)]
    months = (d["mat"].dt.year - d["eff"].dt.year) * 12 + (d["mat"].dt.month - d["eff"].dt.month)
    anniversary = (d["mat"].dt.day - d["eff"].dt.day).abs() <= 3

    rows = []
    for tenor, m in TENOR_MONTHS.items():
        b = d[(months == m) & anniversary]["rate"] * 100
        if len(b) >= min_trades:
            q1, med, q3 = b.quantile([0.25, 0.5, 0.75])
            rows.append({"tenor": tenor, "trades": len(b), "market_pct": round(float(med), 4),
                         "iqr_bp": round(float(q3 - q1) * 100, 1)})
    return rows


def main() -> None:
    """Parses arguments, compares our par nodes with the market and writes the JSON."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--valuation-date", default=None, help="ISO date; default: the book's default")
    p.add_argument("--min-trades", type=int, default=10, help="minimum trades for a tenor to count")
    p.add_argument("--out", default=None, help="output folder (default: data/analysis/gap_review/<date>)")
    a = p.parse_args()
    vdate = (date.fromisoformat(a.valuation_date) if a.valuation_date
             else load_portfolio_config().default_valuation_date)

    cfg = load_pricer_config()
    curve, _as_of, _ = RatesMarketDataAdapter(cfg, _ROOT).par_ois_curve(_build_ctx(cfg, vdate, "PV"))
    ours = {t: r * 100 for t, r in zip(curve.tenors, curve.par_rates, strict=True)}

    rows = market_par_rates(vdate, a.min_trades)
    for r in rows:
        r["ours_pct"] = round(ours[r["tenor"]], 4)
        r["ours_minus_market_bp"] = round((ours[r["tenor"]] - r["market_pct"]) * 100, 1)
    print(pd.DataFrame(rows).to_string(index=False))

    out_dir = a.out or storage.data_path("analysis", "gap_review", vdate.isoformat())
    path = storage.join(str(out_dir), "curve_vs_market.json")
    storage.makedirs_for(path)
    storage.write_text_atomic(path, json.dumps({"valuation_date": vdate.isoformat(), "curve_source": curve.source,
                                                "tenors": rows}, indent=1))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
