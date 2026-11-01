"""Regenerate the numeric sections of the per-trade docs (docs/pricing/trades/TRADE_*.md).

Each page is hand-authored teaching material. This refreshes only the two data-driven sections
— **The trade (economics)** (live notional/params from the active default book) and **Reading
the live diagnostics** (live PV + measures from the stored per-trade result) — and preserves the
hand-written prose (In plain English, Market data, How it's priced, Limitations, code map).

Run after re-pricing the default book:
    python -m src.pricer.runner --valuation-date 2026-10-06
    python scripts/pricer/gen_trade_docs.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.pricer.portfolio.portfolio_loader import resolve_book_path  # noqa: E402

TRADES_DIR = ROOT / "docs" / "pricing" / "trades"
PV_DIR = ROOT / "data" / "pricer" / "PV"
VDATE = "2026-10-06"

ECON_H = "The trade (economics)"
DIAG_H = "Reading the live diagnostics"
RISK_H = "Risk & stress treatment"

_LIQ = json.loads((ROOT / "configs" / "scenariogen" / "liquidity.json").read_text(encoding="utf-8"))
_COL = json.loads((ROOT / "configs" / "scenariogen" / "collateral.json").read_text(encoding="utf-8"))

# Liquidity-horizon rationale by resolved horizon (Basel/FRTB classes, design FINDING 1 / §11.9).
_LH_WHY = {10: "large-cap equity / major rates & FX", 20: "IG credit / rates vol",
           40: "equity vol / single-name CDS", 60: "the illiquid tail (TWD is not freely convertible)"}

# Per-trade stress notes beyond the config-derived horizon + margin: the basis / scenario / crowding
# / driver dimensions the market-intelligence work (P2–P7) attached to each trade. Kept here (not in
# the per-page prose) so they regenerate consistently with the configs.
STRESS_NOTES: dict[str, list[str]] = {
    "TRADE_1": ["**Crowding (F5):** scaled by NVDA days-to-cover percentile (~×1.30 recently — short interest at its own highs) on the dual-RAG idiosyncratic leg, composed with the news multiplier.",
                "**Drivers:** a single-name semis leg of the `basis:SMH_SEMI` dispersion (F3, hedged by #6) and the `style:MOM` momentum crash (F4); amplified in `ai_bust` by the F9 throughput multiplier and re-priced against real rates (F6).",
                "**News (P3):** 8-K item codes + GDELT tone clamp-scale the idiosyncratic shock; a 5.02 filing fires `keyman_nvda` live."],
    "TRADE_2": ["**Taiwan (F8):** this TSM ADR is the exposure the #26 TWD swap hedges; `taiwan_geopolitical` is amplified by the GPR-TWN severity multiplier (×1.32, trigger live) — both legs move together.",
                "**Crowding (F5)** + single-name semis leg of the `basis:SMH_SEMI` dispersion (F3)."],
    "TRADE_3": ["**Crowding (F5)**, `style:MOM` (F4), a single-name leg of `basis:SMH_SEMI` (F3), and the F9 `ai_bust` throughput multiplier."],
    "TRADE_4": ["**Crowding (F5)**, `style:MOM` (F4), a single-name leg of `basis:SMH_SEMI` (F3), and the F9 `ai_bust` throughput multiplier (HBM/memory is the sharp end of the capex cycle)."],
    "TRADE_5": ["**Systematic proxy:** `eq:XLK` is the `ai_bust` systematic driver leg and the default proxy for unmapped underliers; carries `style:MOM` (F4) and real-rate (F6) sensitivity."],
    "TRADE_6": ["**Dispersion basis DRIVER (F3):** this SMH short is the ETF leg of `basis:SMH_SEMI` (SMH − single-name semis) — the `semis_dispersion` scenario is the hedge *failing* (SMH holds while the single names fall), a risk a name-level Σ nets to ~flat.",
                "**Crowding + squeeze (F5):** a *short*, so a crowded SMH short is **squeeze / upside-tail** risk a long-book stress framework never flags — surfaced via the short-squeeze flag when days-to-cover is at its historical tail. (It is the one cash-equity leg tagged margined, F2, because it is short.)"],
    "TRADE_15": ["**Real rates (F6):** an IG corporate whose long-duration discounting is decomposed against real vs breakeven rates in `real_rate_repricing`."],
    "TRADE_16": ["**Real rates (F6):** long-duration IG credit, real/breakeven decomposition in `real_rate_repricing`."],
    "TRADE_17": ["**CDS-bond basis (F3):** this ORCL long-end bond is hedged by the #28 ORCL CDS; the `funding_basis_squeeze` scenario dislocates the CDS-bond basis (a one-sided bump that moves the CDS, not the bond).",
                 "**Real rates (F6):** 2053 maturity — the sharpest real-rate duration in the book."],
    "TRADE_18": ["**CDS-bond basis (F3):** this META long-end bond is hedged by the #29 META CDS (`funding_basis_squeeze`).",
                 "**Real rates (F6):** 2054 maturity."],
    "TRADE_19": ["**Real rates (F6):** IG credit, real/breakeven decomposition."],
    "TRADE_20": ["**Swap-spread basis (F3):** this UST is hedged by the #22 SOFR IRS; `funding_basis_squeeze`'s swap-spread bump moves the Treasury curve, not the swap (bonds discount off OIS).",
                 "**Real rates (F6):** on-the-run 10Y — the belly of the real-rate move."],
    "TRADE_21": ["**Swap-spread basis (F3)** vs the #22 SOFR IRS; real-rate sensitivity (F6, front-end)."],
    "TRADE_22": ["**Swap-spread basis (F3):** the `basis:SWAP_SPREAD` overlay moves only the Treasury/OIS curve, repricing the USTs it hedges (#20/#21), not this swap — the hedge basis widening in `funding_basis_squeeze`.",
                 "**Collateral:** cleared, so a rate shock drives a large variation-margin line (F2)."],
    "TRADE_26": ["**The book's standout liquidity case:** in `taiwan_geopolitical` this trade is simultaneously the *least liquid* (60d, above) and the *most stressed* leg — the single largest exit-value correction in the book.",
                 "**Taiwan (F8):** amplified by the GPR-TWN severity multiplier (×1.32, trigger live) — the same event that stresses it also makes its 60d horizon bite."],
    "TRADE_28": ["**CDS-bond basis (F3):** the `basis:CDS_BOND` bump moves this single-name ORCL CDS (it hedges bond #17), not the bond — the hedge basis in `funding_basis_squeeze`.",
                 "**Jump-to-default / IDR** is carried in the risk engine."],
    "TRADE_29": ["**CDS-bond basis (F3):** single-name META CDS hedging bond #18 (`funding_basis_squeeze`); JTD/IDR in the risk engine."],
    "TRADE_30": ["**Index credit:** CDX.NA.IG (sell protection) takes the 20d IG-credit horizon."],
    "TRADE_31": ["**AI-power diversifier (Part 2):** Constellation (nuclear) long, with its OWN VaR/Σ factor (`EQ_CEG`) — the model credits its real (lower) correlation to semis *and* its higher single-name vol, so it is sized on its own risk rather than as XLK beta (which had over-sized it)."],
    "TRADE_32": ["**AI-power diversifier (Part 2):** Vistra (IPP) long, own factor (`EQ_VST`) — same as #31."],
    "TRADE_33": ["**Hyperscaler (Part 2):** Alphabet long — AI demand/monetization; own factor (`EQ_GOOGL`)."],
    "TRADE_34": ["**Gold ballast (Part 2):** GLD's own factor (`EQ_GLD`) is low/negatively correlated to tech, so it *reduces* book risk in an AI-equity drawdown (a negative risk contribution) — the strongest diversifier of the new sleeves."],
    "TRADE_35": ["**Convexity sleeve (Part 2):** a deeper-OTM SPX put carried as an un-thinned tail hedge — pays off convexly in a sharp sell-off, re-symmetrizing the book's boom/bust profile."],
    "TRADE_36": ["**Liquidity buffer (Part 2):** short-duration UST held as dry powder. Low front-end DV01, default-free; carries real-rate (F6) front-end sensitivity; negligible stress P&L."],
    "TRADE_37": ["**Semis dispersion pair (N5):** INTC long, the laggard leg of the beta-neutral INTC/AMD pair (#38 short). Own factor (`EQ_INTC`); the pair expresses intra-semis relative value (laggard catch-up vs the crowded leader) with net-zero market beta — orthogonal alpha, not more AI-beta."],
    "TRADE_38": ["**Semis dispersion pair (N5):** AMD short, the leader leg. Own factor (`EQ_AMD`); the sizing `market_neutral_pairs` feature pins it to 0.636× the INTC long (= β_INTC/β_AMD) so the pair is beta-neutral."],
}
# option underliers get a per-name eqvol note (B4) + real-rate; keyed by trade for the 8 options.
for _t in ("TRADE_7", "TRADE_8", "TRADE_9", "TRADE_10", "TRADE_11", "TRADE_12", "TRADE_13", "TRADE_14", "TRADE_35"):
    STRESS_NOTES.setdefault(_t, []).append(
        "**Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied "
        "tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. "
        "market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.")


def _liquidity_horizon(trade: dict) -> int:
    """Resolve the F1 liquidity horizon exactly as src/scenariogen/liquidity.py does."""
    hz = _LIQ["horizon_days"]
    tt = trade["trade_type"]
    p = trade.get("params", {})
    ccy = str(p.get("foreign_ccy", "")).upper()
    if ccy and ccy in hz.get("by_foreign_ccy", {}):
        return int(hz["by_foreign_ccy"][ccy])
    if tt == "cds" and p.get("kind") == "index":
        return int(hz.get("cds_index_horizon_days", 20))
    return int(hz.get("by_trade_type", {}).get(tt, _LIQ.get("base_horizon_days", 10)))


def _is_margined(tid: str, trade: dict) -> bool:
    ov = _COL.get("margined_overrides", {})
    if tid in ov:
        return bool(ov[tid])
    return trade["trade_type"] in set(_COL.get("margined_trade_types", []))


def _risk_stress(tid: str, trade: dict) -> str:
    lh = _liquidity_horizon(trade)
    margined = _is_margined(tid, trade)
    lines = [
        "*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). "
        "Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and "
        "stamped into the scenario report.*\n",
        f"- **Liquidity horizon (F1):** {lh}d — {_LH_WHY.get(lh, 'per Basel/FRTB class')}; a "
        f"`√({lh}/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.",
        f"- **Collateral (F2):** {'**margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.' if margined else 'unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).'}",
    ]
    lines += ["- " + n for n in STRESS_NOTES.get(tid, [])]
    return "\n".join(lines) + "\n"

_MONEY = {"notional", "usd_notional", "pv", "dv01", "cash_delta", "delta_cash",
          "gross_notional", "vega_1volpt", "accrued_usd", "npv"}
_PCT = {"fair_rate", "fixed_rate", "foreign_fixed_rate", "usd_fixed_rate", "ytm",
        "dividend_yield", "mtm_spread", "par_spread"}


def _money(v: float) -> str:
    a = abs(v); s = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{s}${a/1e9:,.2f}bn"
    if a >= 1e6:
        return f"{s}${a/1e6:,.2f}mm"
    return f"{s}${a:,.0f}"


def _fmt(key: str, v: object) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        k = key.lower()
        if k in _MONEY:
            return _money(float(v))
        if k in _PCT:
            return f"{float(v)*100:.3f}%"
        if abs(v) >= 1000 or (isinstance(v, int) and abs(v) >= 100):
            return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
        return f"{v:,.4g}"
    return str(v)


def _economics(trade: dict, profile: str) -> str:
    rows = [f"| Notional | {_money(float(trade.get('notional', 0)))} ({profile} book) |"]
    p = trade.get("params", {})
    for k, v in p.items():
        if k == "legs":
            for i, leg in enumerate(v):
                desc = " ".join(f"{lk}={lv}" for lk, lv in leg.items())
                rows.append(f"| Leg {i+1} | {desc} |")
        elif v is not None:
            rows.append(f"| {k.replace('_', ' ')} | {_fmt(k, v)} |")
    rows.append(f"| Currency | {trade.get('currency', 'USD')} |")
    return ("Contractual terms from the active default book "
            f"(**{profile}** sizing — `portfolio_{profile}.json`):\n\n"
            "| Term | Value |\n|---|---|\n" + "\n".join(rows) + "\n")


def _diagnostics(res: dict, profile: str) -> str:
    pv = res.get("pv")
    measures = res.get("measures", {}) or {}
    lines = [f"Live valuation at **{VDATE}** on the **{profile}** book (real ~4% SOFR curve): "
             f"**PV = {_money(float(pv))}**.\n"]
    if measures:
        lines.append("| Measure | Value |\n|---|---|")
        for k, v in measures.items():
            lines.append(f"| `{k}` | {_fmt(k, v)} |")
    legs = res.get("leg_pvs", {}) or {}
    if legs:
        lines.append("\nLeg PVs: " + ", ".join(f"`{k}` = {_money(float(v))}" for k, v in legs.items()) + ".")
    lines.append("\n_Regenerated from the stored per-trade result "
                 f"(`data/pricer/PV/<asset>/<trade>/{VDATE}.json`) by "
                 "`scripts/pricer/gen_trade_docs.py`._")
    return "\n".join(lines) + "\n"


def _split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (preamble, [(header, body), ...]) splitting on level-2 '## ' headers."""
    parts = re.split(r"(?m)^## (.+)$", text)
    preamble = parts[0]
    sections = [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts), 2)]
    return preamble, sections


def _pv_path(trade_id: str) -> Path | None:
    hits = list(PV_DIR.glob(f"*/{trade_id}/{VDATE}.json"))
    return hits[0] if hits else None


def main() -> int:
    book = json.loads(resolve_book_path(None).read_text(encoding="utf-8"))
    profile = book.get("sizing_basis", {}).get("profile", "aggressive")
    trades = {t["trade_id"]: t for t in book["trades"]}

    n_updated = n_missing = 0
    for page in sorted(TRADES_DIR.glob("TRADE_*.md")):
        tid = page.stem
        trade = trades.get(tid)
        pv_path = _pv_path(tid)
        if trade is None or pv_path is None:
            print(f"  skip {tid} (trade={trade is not None}, pv={pv_path is not None})")
            n_missing += 1
            continue
        res = json.loads(pv_path.read_text(encoding="utf-8"))
        preamble, sections = _split_sections(page.read_text(encoding="utf-8"))
        risk_body = "\n\n" + _risk_stress(tid, trade) + "\n"
        new_sections = []
        for header, body in sections:
            if header == ECON_H:
                body = "\n\n" + _economics(trade, profile) + "\n"
            elif header == DIAG_H:
                body = "\n\n" + _diagnostics(res, profile) + "\n"
            elif header == RISK_H:
                body = risk_body                        # refresh in place on re-run
            new_sections.append((header, body))
        # insert the Risk & stress section (once) right after the live diagnostics, before Limitations
        if RISK_H not in {h for h, _ in new_sections}:
            idx = next((i for i, (h, _) in enumerate(new_sections) if h == DIAG_H), len(new_sections) - 1)
            new_sections.insert(idx + 1, (RISK_H, risk_body))
        out = preamble + "".join(f"## {h}{b}" for h, b in new_sections)
        page.write_text(out, encoding="utf-8")
        n_updated += 1
    print(f"\nRegenerated {n_updated} trade pages (profile={profile}, {VDATE}); {n_missing} skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
