"""Feed -> consumption map: which pricing/risk path (if any) actually reads a feed.

The catalog's legacy "trades" count is asset-class-level — every feed in a folder
inherits the same number whether or not any trade reads it. That over-attributes
(unused feeds look consumed) and confuses "ungated" with "unused". This module
encodes the *real* linkage, derived from the pricer loader adapters + risk factor
config, so the UI can show, per feed:

  * ``consumed_by`` — coarse bucket for the catalog column: ``both`` (PV + risk),
    ``pv``, ``risk``, or ``none``.
  * ``category`` — the fine reason: ``pv+risk`` / ``pv`` / ``risk`` for consumed
    feeds; ``reference`` / ``redundant`` / ``roadmap`` / ``legacy`` for the rest.
  * ``role`` — one line on what the feed does, or why it is not a pricing input.
  * ``pv_trades`` — the actual trades that read the feed at valuation time
    (computed live from the portfolio, so it tracks book changes).

Ground truth: src/pricer/loader/*_adapter.py (PV), configs/pricer/pricer_config.json
risk.var.factors + src/pricer/risk/factor_history.py (VaR), and the market-data
audit (docs/design/AITrade_quantlib_portfolio_marketdata.md §5).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# --- portfolio predicates (a feed's real PV consumers) --------------------- #
Meta = dict[str, Any]


def _num(tid: str) -> int:
    tail = tid.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _sofr(m: Meta) -> bool:
    # SOFR-OIS is the discount (and forecast) curve behind rates, credit, FX,
    # option and corporate-bond valuation; the on-the-run USTs use the UST curve.
    ac = m.get("asset_class")
    if ac in ("rates", "credit", "fx", "options"):
        return True
    return ac == "bonds" and m.get("trade_type") == "corp_bond"


def _ust(m: Meta) -> bool:
    return m.get("asset_class") == "bonds" and m.get("trade_type") == "ust_bond"


def _single_name_cds(m: Meta) -> bool:
    p = m.get("params", {})
    if m.get("asset_class") == "credit" and p.get("kind") == "single_name":
        return True
    # 2026-07 reconciliation: ORCL/META corporate-bond credit spreads now come
    # from this same single-name feed.
    return (m.get("asset_class") == "bonds"
            and m.get("trade_type") == "corp_bond"
            and p.get("issuer") in ("ORCL", "META"))


def _index_cds(m: Meta) -> bool:
    return m.get("asset_class") == "credit" and m.get("params", {}).get("kind") == "index"


def _equity_stock(m: Meta) -> bool:
    return m.get("asset_class") == "equity" and m.get("trade_type") == "stock"


def _fx_ccy(ccy: str) -> Callable[[Meta], bool]:
    return lambda m: (m.get("asset_class") == "fx"
                      and m.get("params", {}).get("foreign_ccy") == ccy)


def _underlier(u: str) -> Callable[[Meta], bool]:
    return lambda m: (m.get("asset_class") == "options"
                      and m.get("params", {}).get("underlier") == u)


def _rates_vol(m: Meta) -> bool:
    # the vol-sensitive rates trades (swaptions + cap) whose normal-vol LEVEL is
    # set point-in-time from the MOVE index; the plain OIS swap #22 uses no vol.
    return m.get("trade_type") in ("european_swaption", "bermudan_swaption", "cap")


# --- static registry: feed_id -> (category, role, predicate|None) ---------- #
# category ∈ {pv+risk, pv, risk, reference, redundant, roadmap, legacy}
_PV = "pv"
_R = "risk"
_PVR = "pv+risk"

_REGISTRY: dict[str, tuple[str, str, Callable[[Meta], bool] | None]] = {
    # ---- PV pricing feeds (some are also risk drivers) ----
    "rates/pensford_sofr_forward.csv": (
        _PVR, "SOFR-OIS discount/forecast curve — the binding discounting input for "
        "rates, corporate bonds, credit, FX and options; also a VaR/scenario driver.", _sofr),
    "bonds/treasury_par_yields.csv": (
        _PV, "UST par-yield discount curve for the on-the-run Treasuries.", _ust),
    "credit/dtcc_single_name_cds.csv": (
        _PV, "5Y single-name par CDS spread — prices the single-name CDS trades and "
        "(2026-07 reconciliation) the ORCL/META corporate-bond credit spreads.", _single_name_cds),
    "credit/dtcc_index_cds.csv": (
        _PVR, "CDX.NA.IG index par spread for the index CDS; capped credit fallback in "
        "full-reval VaR.", _index_cds),
    "equity/prices_daily.csv": (
        _PVR, "Daily close — cash-equity spot marks; per-name equity-delta VaR factors.", _equity_stock),
    "equity/dividends.csv": (
        _PV, "Trailing-12M dividend yield folded into the cash-equity marks.", _equity_stock),
    "fx/ecb_fx_rates.csv": (
        _PVR, "USD/JPY spot (ECB cross) for the JPY forward; FX-delta VaR factor.", _fx_ccy("JPY")),
    "fx/fed_h10_fx_rates.csv": (
        _PVR, "USD/TWD spot (Fed H.10) for the TWD cross-currency swap; FX-delta VaR factor.", _fx_ccy("TWD")),
    "fx/boj_tonar.csv": (
        _PVR, "JPY foreign discount rate (BoJ TONAR) for the JPY forward; FX-rate VaR factor.", _fx_ccy("JPY")),
    "stress/move_index.csv": (
        _PVR, "MOVE level — sets the swaption/cap normal-vol level point-in-time (#23-25) "
        "and is the rates-vol VaR factor.", _rates_vol),
    # ---- risk / VaR only (not a PV input) ----
    "rates/fred_rates.csv": (
        _R, "FRED Treasury par rates — IR key-rate VaR factors (the PV curve is Pensford).", None),
    "stress/fred_stress_macro.csv": (
        _R, "BofA IG OAS — the credit (CS01) VaR factor.", None),
    "options/vol_indices/VIX_History.csv": (
        _R, "VIX — equity-vega VaR factor for the option underliers + vol-term base.", None),
    "options/vol_indices/VIX3M_History.csv": (
        _R, "VIX3M — equity vol term-structure VaR factor.", None),
    "options/vol_indices/SKEW_History.csv": (
        _R, "CBOE SKEW — equity vol-skew VaR factor.", None),
    "options/vol_indices/VXAPL_History.csv": (
        _R, "AAPL vol index — AAPL per-name vega (sensitivity VaR).", None),
    # ---- downloaded but not a pricing/risk input ----
    "bonds/dtcc_corp_cds_spreads.csv": (
        "legacy", "Retired corp-CDS feed (no longer downloaded or read): its action filter matched "
        "'NEW' exactly, so it held no new trades. Issuer spreads come from the credit-desk "
        "single-name feed. Safe to delete if still on disk.", None),
    "bonds/finra_treasury_daily.csv": (
        "redundant", "FINRA treasury prints — the UST curve is built from Treasury.gov par yields instead.", None),
    "bonds/treasurydirect_announced.csv": (
        "reference", "On-the-run auction reference (CUSIP/terms); bond economics come from the trade tickets.", None),
    "bonds/treasurydirect_auctioned.csv": (
        "reference", "Auction-result reference; not a pricing input.", None),
    "bonds/edgar_bond_filings.csv": (
        "reference", "SEC bond reference / prospectus data; not a pricing input.", None),
    "rates/nyfed_sofr_rates.csv": (
        "reference", "Realized SOFR fixings — a curve-front cross-check only; PV uses the "
        "Pensford forward path.", None),
    "rates/pensford_historical_sofr.csv": (
        "redundant", "Historical realized SOFR; PV needs the forward curve (pensford_sofr_forward).", None),
    "rates/dtcc_sdr_irs.csv": (
        "roadmap", "DTCC SDR swap prints — alternative par-OIS source; superseded by Pensford.", None),
    "rates/dtcc_sdr_ir_options.csv": (
        "roadmap", "DTCC SDR swaption/cap premiums — intended strike/expiry vol smile. The vol LEVEL "
        "is MOVE-derived. The earlier 'unusable to calibrate' finding (~20 of 540 rows) came from a "
        "file collapsed to one row per day by a dedup bug (fixed in sdr-dedup; option type and strike "
        "are now kept) — re-assess on the full tape.", None),
    "fx/cbc_twd_ndf_fix.csv": (
        "roadmap", "CBC TWD NDF fixings — the TWD swap holds the foreign rate constant "
        "(no free TAIBOR / xccy-basis curve), so the fixing is not needed by the simplified model.", None),
    "equity/prices_adj_close.csv": (
        "redundant", "Split-adjusted close — the pricer/risk use the raw close (prices_daily).", None),
    "equity/splits.csv": (
        "reference", "Corporate-action reference; PV is point-in-time.", None),
    "reference/edgar_xbrl_facts.csv": (
        "legacy", "DEPRECATED 2026-07 (harness-audit F2): issuer fundamentals now sourced from "
        "ARag facts_lookup (cited). Standalone feed disabled and no longer refreshed.", None),
    "reference/instruments_figi.csv": (
        "reference", "Security-identifier (FIGI) mapping.", None),
    "stress/eia_power_demand.csv": (
        "reference", "AI power-demand series for the stress narrative (scenariogen); not a pricing input.", None),
    "options/vol_indices/VIX6M_History.csv": (
        "redundant", "Downloaded in the CBOE vol-index batch; no VaR factor maps to it.", None),
    "options/vol_indices/VIX9D_History.csv": (
        "redundant", "Downloaded in the CBOE vol-index batch; no VaR factor maps to it.", None),
    "options/vol_indices/VVIX_History.csv": (
        "redundant", "Vol-of-vol index; no factor consumes it.", None),
    "options/vol_indices/VXAZN_History.csv": (
        "redundant", "AMZN vol index; AMZN is not an option underlier in the book.", None),
    "options/vol_indices/VXGOG_History.csv": (
        "redundant", "GOOG vol index; GOOG is not an option underlier in the book.", None),
}

_CATEGORY_TO_BUCKET = {
    _PVR: "both", _PV: "pv", _R: "risk",
    "reference": "none", "redundant": "none", "roadmap": "none", "legacy": "none",
}


def _entry_for(feed_id: str) -> tuple[str, str, Callable[[Meta], bool] | None]:
    if feed_id in _REGISTRY:
        return _REGISTRY[feed_id]
    # option chains are per-underlier: options/chains/<UND>_chains.csv
    if feed_id.startswith("options/chains/") and feed_id.endswith("_chains.csv"):
        und = feed_id.split("/")[-1][: -len("_chains.csv")]
        return (_PV, f"{und} option chain — spot, dividend yield and per-strike implied "
                f"vol (recovered via put-call parity) for the {und} options.", _underlier(und))
    return ("unclassified", "Not classified.", None)


def feed_consumption(feed_id: str, portfolio: dict[str, Meta]) -> dict[str, Any]:
    """Return the consumption record for a feed, with live PV-trade linkage."""
    category, role, pred = _entry_for(feed_id)
    pv_trades: list[dict[str, str]] = []
    if pred is not None:
        ids = sorted((tid for tid, m in portfolio.items() if pred(m)), key=_num)
        pv_trades = [{"trade_id": tid, "description": portfolio[tid].get("description", "")}
                     for tid in ids]
    return {
        "consumed_by": _CATEGORY_TO_BUCKET.get(category, "none"),
        "category": category,
        "role": role,
        "pv_trades": pv_trades,
        "n_pv_trades": len(pv_trades),
    }
