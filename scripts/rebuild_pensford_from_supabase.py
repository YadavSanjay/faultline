"""Rebuild the Pensford SOFR forward-curve file with FULL forward paths from Supabase.

Why this exists
---------------
``data/marketdata/rates/pensford_sofr_forward.csv`` had only ONE forward point per
snapshot (the archive-xlsx scrape used to collapse the monthly path because
``upsert_csv(date_col="as_of_date")`` dedupped on as_of alone — now fixed to de-dup on
``(as_of_date, settlement_date)``). With a single point the OIS curve builder
(``rates_adapter._discount_factors``) can't compound a path, so the SOFR discount curve
degenerates to ~0% and every swap/bond/CDS discounts at zero. This script remains the way
to pull the *current* curve, since the Pensford archive only publishes month-end files.

The Pensford *archive* web page only publishes month-end files through 2026-05-29, but the
live ``/forward-curve`` page is backed by a public Supabase (PostgREST) database with the
full curve:
  * ``forward_curve``          — the CURRENT curve (updated daily), as_of = first reset date
  * ``archive_forward_curve``  — historical snapshots keyed by ``as_of_date``
Both expose a daily ``1M Term SOFR`` projection path (rate_value in percent). The anon key
is the public key embedded in the page (read-only, row-level-security limited); set it as
``PENSFORD_SUPABASE_ANON_KEY`` in ``.env``.

What this does
--------------
Rebuilds the CSV so every snapshot carries a full MONTHLY forward path:
  * the current curve from ``forward_curve`` (2026-10-06 at the last run);
  * each historical snapshot present in the existing file that ``archive_forward_curve``
    also has (e.g. 2026-05-29), backfilled to a real path;
  * snapshots the archive DB lacks (old 2020-2023 month-ends we never value at) are kept
    as-is.
Output columns match the existing schema: as_of_date, settlement_date, forward_sofr
(decimal), forward_sofr_upper (left blank — the +2sd band is cosmetic, unused in pricing).

Run:  python scripts/rebuild_pensford_from_supabase.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
FWD_FILE = _ROOT / "data" / "marketdata" / "rates" / "pensford_sofr_forward.csv"

SUPABASE = "https://daectsfxmldypacvammr.supabase.co/rest/v1"
# The anon key is the public one embedded in https://www.pensford.com/forward-curve (read-only). It
# is Pensford's, not ours, so it is not committed: copy it from the page's Supabase requests
# (browser dev tools, Network tab, the `apikey` request header) into .env.
ANON_KEY_ENV = "PENSFORD_SUPABASE_ANON_KEY"
_LABEL = "1M Term SOFR"
CURRENT_AS_OF = "2026-07-10"   # the forward_curve table's first reset (its as_of)


def _headers() -> dict[str, str]:
    """Supabase auth headers from the anon key in the environment or .env; raises if unset."""
    load_dotenv(_ROOT / ".env")
    key = os.environ.get(ANON_KEY_ENV, "")
    if not key:
        raise SystemExit(f"set {ANON_KEY_ENV} in .env (see the comment above ANON_KEY_ENV)")
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _fetch_path(table: str, as_of: str | None) -> pd.DataFrame:
    """Fetch the 1M Term SOFR projection path (reset_date, rate_value) from a table."""
    params = {
        "select": "reset_date,rate_value",
        "rate_label": f"eq.{_LABEL}",
        "category": "eq.projection",
        "order": "reset_date.asc",
        "limit": "100000",
    }
    if as_of is not None:
        params["as_of_date"] = f"eq.{as_of}"
    resp = requests.get(f"{SUPABASE}/{table}", headers=_headers(), params=params, timeout=60)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return pd.DataFrame(columns=["reset_date", "rate_value"])
    return pd.DataFrame(rows)


def _monthly_path(df: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """Downsample a daily projection to one point per calendar month (the monthly grid)."""
    df = df.copy()
    df["settlement_date"] = pd.to_datetime(df["reset_date"])
    df = df.sort_values("settlement_date").drop_duplicates("settlement_date")
    df["ym"] = df["settlement_date"].dt.to_period("M")
    monthly = df.groupby("ym", as_index=False).first()      # first reset of each month
    return pd.DataFrame({
        "as_of_date": as_of,
        "settlement_date": monthly["settlement_date"].dt.strftime("%Y-%m-%d"),
        "forward_sofr": (monthly["rate_value"].astype(float) / 100.0).round(6),
        "forward_sofr_upper": "",
    })


def main() -> int:
    existing = pd.read_csv(FWD_FILE)
    existing_as_ofs = sorted(existing["as_of_date"].astype(str).unique())
    print(f"Existing file: {len(existing)} rows, {len(existing_as_ofs)} snapshots "
          f"({existing_as_ofs[0]}..{existing_as_ofs[-1]})")

    frames: list[pd.DataFrame] = []
    backfilled, kept = [], []

    # 1. Current curve from the live forward_curve table (as_of = its first reset).
    cur = _fetch_path("forward_curve", None)
    if not cur.empty:
        first_reset = pd.to_datetime(cur["reset_date"]).min().strftime("%Y-%m-%d")
        as_of = first_reset if first_reset else CURRENT_AS_OF
        m = _monthly_path(cur, as_of)
        frames.append(m)
        backfilled.append(f"{as_of} (current, {len(m)} monthly pts)")

    # 2. Backfill each existing historical snapshot from archive_forward_curve.
    for as_of in existing_as_ofs:
        if any(as_of in b for b in backfilled):     # already have it as the current curve
            continue
        arch = _fetch_path("archive_forward_curve", as_of)
        if not arch.empty and len(arch) > 3:
            frames.append(_monthly_path(arch, as_of))
            backfilled.append(f"{as_of} ({len(arch)} daily -> monthly)")
        else:
            frames.append(existing[existing["as_of_date"].astype(str) == as_of])
            kept.append(as_of)

    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["as_of_date", "settlement_date"]).reset_index(drop=True)
    out.to_csv(FWD_FILE, index=False)

    print(f"\nBackfilled {len(backfilled)} snapshots with full paths:")
    for b in backfilled:
        print(f"  + {b}")
    print(f"Kept {len(kept)} snapshots as-is (not in archive DB): "
          f"{kept[:3]}{'...' if len(kept) > 3 else ''}")
    print(f"\nWrote {len(out)} rows -> {FWD_FILE}")
    print(f"Snapshots now: {out['as_of_date'].nunique()} "
          f"({out['as_of_date'].min()}..{out['as_of_date'].max()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
