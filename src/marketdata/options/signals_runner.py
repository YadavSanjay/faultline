"""Derive option-implied signals from the chain snapshots (market-intelligence B1).

A derivation runner, not a downloader: it reads the CBOE chains already on disk (from both
`chains/` and `chains_signals/`), computes the point-in-time signals in `signals.py` for every
(underlying, snapshot_date), and writes one tidy CSV + provenance sidecar:

    data/marketdata/options/signals/option_signals.csv

Usage:
    python -m src.marketdata.options.signals_runner
    python -m src.marketdata.options.signals_runner --underlyings NVDA ORCL

Dual-mode: all reads go through `src.common.storage` and the write goes through the marketdata
`data_store` (façade-resolved), so it runs unchanged under PRICER_DEPLOY_MODE local and aws.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common import storage
from src.common.config.loader import load_json
from src.marketdata.common.config_loader import get_project_root, load_config
from src.marketdata.common.data_store import get_output_path, save_csv
from src.marketdata.common.observability import begin_run, record_run
from src.marketdata.options.credit_implied import build_credit_table
from src.marketdata.options.rnd import atm_expected_move, recover_forward_curve, risk_neutral_density
from src.marketdata.options.signals import SIGNAL_COLUMNS, compute_signals

logger = logging.getLogger(__name__)


def _closes_by_ticker(prices_loc: str) -> dict[str, pd.DataFrame]:
    """Ascending (date, close) frames per ticker from prices_daily, for realized vol / VRP.

    Returns an empty map (and the runner degrades to null VRP) if the file is absent.
    """
    if not storage.exists(prices_loc):
        logger.warning("signals: prices file %s not found -- realized vol / VRP will be null", prices_loc)
        return {}
    df = storage.read_csv(prices_loc)
    if not {"date", "ticker", "close"} <= set(df.columns):
        logger.warning("signals: prices file lacks date/ticker/close -- realized vol / VRP will be null")
        return {}
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    return {str(tkr): g[["date", "close"]] for tkr, g in df.groupby("ticker")}


def _chain_locs(md_base: str, chain_dirs: list[str]) -> list[str]:
    locs: list[str] = []
    for d in chain_dirs:
        locs.extend(storage.glob(storage.join(md_base, "options", d), "*_chains.csv"))
    return sorted(locs)


def run(underlyings: list[str] | None = None) -> None:
    handle = begin_run("options")
    cfg = load_config("options")
    scfg = cfg.get("signals", {})
    if not scfg.get("enabled", True):
        logger.info("signals: disabled in config -- skipping")
        record_run(handle, {"signals": "skipped (disabled)"})
        return

    md_base = storage.join(storage.data_base(_ROOT), "marketdata")
    closes = _closes_by_ticker(storage.join(md_base, *scfg.get("prices_file", "equity/prices_daily.csv").split("/")))
    want = {u.upper() for u in underlyings} if underlyings else None

    params: dict[str, Any] = {
        "min_iv": float(scfg.get("min_iv", 0.03)),
        "max_iv": float(scfg.get("max_iv", 2.5)),
        "min_oi": int(scfg.get("min_oi", 0)),
        "target_dte": int(scfg.get("target_dte", 30)),
        "back_dte": int(scfg.get("back_dte", 90)),
        "rv_short": int(scfg.get("realized_vol_short", 20)),
        "rv_long": int(scfg.get("realized_vol_long", 60)),
    }

    target_dte = params["target_dte"]
    rows: list[dict[str, object]] = []
    curves: list[pd.DataFrame] = []
    for loc in _chain_locs(md_base, scfg.get("chain_dirs", ["chains", "chains_signals"])):
        name = loc.replace("\\", "/").rsplit("/", 1)[-1].removesuffix("_chains.csv")
        if want is not None and name.upper() not in want:
            continue
        chain = storage.read_csv(loc)
        if chain.empty or "snapshot_date" not in chain.columns:
            logger.warning("signals %s: empty or missing snapshot_date -- skipped", name)
            continue
        chain["snapshot_date"] = chain["snapshot_date"].astype(str)
        under = str(chain["underlying"].iloc[0]) if "underlying" in chain.columns else name
        tkr_hist = closes.get(under)
        for snap, snap_chain in chain.groupby("snapshot_date"):
            hist = None
            if tkr_hist is not None:
                sub = tkr_hist[tkr_hist["date"] <= pd.Timestamp(str(snap))]
                hist = sub["close"] if not sub.empty else None
            row = compute_signals(snap_chain, under, str(snap), hist, **params)
            _add_b2(row, snap_chain, under, str(snap), target_dte, params, curves)
            rows.append(row)
        logger.info("signals %s: %d snapshot(s)", under, chain["snapshot_date"].nunique())

    if not rows:
        logger.warning("signals: no chains matched -- nothing written")
        record_run(handle, {"signals": "no data"})
        return

    out_dir = scfg.get("output_dir", "signals")
    out = pd.DataFrame(rows, columns=SIGNAL_COLUMNS).sort_values(["underlying", "snapshot_date"])
    out_path = get_output_path("options", f"{out_dir}/{scfg.get('output_file', 'option_signals.csv')}")
    save_csv(out, out_path)
    logger.info("signals: wrote %d rows (%d underliers) -> %s",
                len(out), out["underlying"].nunique(), out_path)

    n_curves = 0
    if curves:
        rnd_df = pd.concat(curves, ignore_index=True)
        rnd_path = get_output_path("options", f"{out_dir}/{scfg.get('rnd_file', 'rnd_curves.csv')}")
        save_csv(rnd_df, rnd_path)
        n_curves = rnd_df[["underlying", "snapshot_date"]].drop_duplicates().shape[0]
        logger.info("signals: wrote %d RND curves -> %s", n_curves, rnd_path)

    n_credit = _write_credit_table(out, scfg, out_dir)
    record_run(handle, {"signals": f"ok ({len(out)} rows, {n_curves} RND curves, {n_credit} credit)"})


def _write_credit_table(signals: pd.DataFrame, scfg: dict[str, Any], out_dir: str) -> int:
    """B3 — Merton option-implied credit spread + CDS cross-check, one row per (issuer, snapshot).

    Reads the committed issuer-fundamentals config (debt + shares) and consumes the spot/vol the
    B1/B2 signals already produced. Skips cleanly if the config is absent so the run still
    completes with just B1/B2.
    """
    rel = scfg.get("fundamentals_file", "options/issuer_fundamentals.json")
    cfg_path = get_project_root() / "configs" / "marketdata" / rel
    if not cfg_path.exists():
        logger.warning("signals: fundamentals config %s not found -- skipping B3 credit table", cfg_path)
        return 0
    fundamentals = load_json(cfg_path)
    table = build_credit_table(signals, fundamentals)
    if table.empty:
        logger.warning("signals: no issuers matched fundamentals -- no credit table written")
        return 0
    path = get_output_path("options", f"{out_dir}/{scfg.get('credit_file', 'credit_implied.csv')}")
    save_csv(table, path)
    latest = table.sort_values("snapshot_date").groupby("underlying").tail(1)
    for _i, r in latest.iterrows():
        xcheck = ("" if pd.isna(r["cds_reference_bp"])
                  else f" vs CDS {r['cds_reference_bp']:.0f}bp (basis {r['basis_bp']:+.0f}bp)")
        logger.info("  credit %-5s CreditGrades %.0fbp (lev %.1f%%, Merton DD %.2f)%s",
                    r["underlying"], r["cg_spread_bp"], 100 * r["leverage"], r["merton_dd"], xcheck)
    logger.info("signals: wrote %d credit rows (%d issuers) -> %s",
                len(table), table["underlying"].nunique(), path)
    return int(table["underlying"].nunique())


def _add_b2(row: dict[str, Any], snap_chain: pd.DataFrame, under: str, snap: str,
            target_dte: int, params: dict[str, Any], curves: list[pd.DataFrame]) -> None:
    """Fill the B2 fields (signal 3 expected move + signal 5 RND tail probs) on a signal row.

    Best-effort per snapshot: a name whose chain is too thin to recover a forward or fit a smile
    keeps the B1 signals and leaves the B2 fields null, rather than sinking the run.
    """
    try:
        fwd_spot, fwds = recover_forward_curve(snap_chain)
        em = atm_expected_move(snap_chain, fwds)
        if em is not None:
            row["expected_move_front"], row["expected_move_front_dte"] = em[0], em[1]
        rnd = risk_neutral_density(
            snap_chain, under, snap, target_dte, spot=fwd_spot,
            min_iv=params["min_iv"], max_iv=params["max_iv"])
        if rnd is not None:
            row["rnd_dte"], row["rnd_forward"], row["rnd_spot"] = rnd.dte, rnd.forward, rnd.spot
            row.update(rnd.tail)
            curves.append(rnd.curve_frame())
    except Exception as exc:  # a numerically awkward chain must not fail the whole derivation
        logger.warning("signals %s @ %s: B2 (expected move / RND) skipped: %s", under, snap, exc)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Derive option-implied signals from CBOE chain snapshots")
    p.add_argument("--underlyings", nargs="+", default=None,
                   help="Restrict to these underliers (default: all chains on disk)")
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    run(underlyings=_parse_args().underlyings)
