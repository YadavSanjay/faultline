# Pricing-module enhancement program (2026-07-12)

> **Status (as of 2026-07): DELIVERED.** Items A1–A3, B1–B5, and C1 are implemented in the pricer
> (SOFR curve past 10Y, piecewise-hazard CDS, issuer Z-spread term structure + SOFR-Treasury
> basis, discrete-dividend American options, BlackVarianceSurface, CBC TWD NDF, untranched CDX +
> index factor, rec01, expiry-dependent rates-vol term structure). The per-instrument methodology
> and per-trade pages under [docs/pricing/](../pricing/README.md) reflect them. This document is
> retained as the governing roadmap/rationale.

Governing plan for the pricing-quality review of 2026-07-12. Scope approved: **full
A→B→C program, phased** — each item lands to `main` on its own with backend + tests +
**full UI (numbers + charts + prose)** + docs, matching how prior workstreams landed.

This document is the roadmap. Each item below is a self-contained unit of work with an
acceptance bar. Items are ordered by ROI; later items may depend on earlier ones.

---

## Why this program (the three recurring themes)

The engines are sound (idiomatic QuantLib: `MakeOIS`, Bachelier swaption/cap,
calibrated Hull-White + tree/FD for the Bermudan, `FdBlackScholesVanillaEngine`,
`IsdaCdsEngine`, CIP-consistent FX forwards, par-bond UST bootstrap). The quality gap
is in the **inputs**:

1. **Curves truncated at 10Y** while the book runs to 2054 (ORCL/META long bonds, the
   5Y×10Y swaption underlying). ~20Y of Pensford forward data is discarded.
2. **Volatility is a single flat number** wherever options live (rates vol; equity FD
   legs collapse a good surface to `BlackConstantVol`).
3. **Credit / foreign curves collapsed to one point** when richer data is on disk
   (single-name CDS term structure for ORCL; TWD NDF fixings; JPY strip).

Several fixes use data we **already download but never read** (18 unused feeds).

---

## Cross-cutting: how each item reflects in the UI

Per the approved "full UI" depth, every backend item ships with:

- **Numbers** — new intermediates added to the relevant `_explain_*` in
  [`src/pricer/lineage.py`](../../src/pricer/lineage.py); they flow through the generic
  `computation` / `tables` slots and render on the Explain screen. Re-run
  `src/pricer/lineage_runner.py` to repopulate `data/pricer/lineage/`.
- **Charts** — new visualisation types (vol-surface heatmap, hazard/basis-curve lines)
  added to `ui/app/src/features/pricing/CalculationLineage.tsx` using the existing
  `ui/app/src/components/shared/{Heatmap,CurveChart}.tsx` (+ a `LineageResponse` field
  in `ui/app/src/lib/api.ts` when a new typed shape is needed).
- **Prose (shared)** — the relevant `docs/pricing/methodology/*.md` (surfaced on every
  affected trade's "How it's priced" methodology tab).
- **Prose (per-trade page)** — for each affected trade, update the hand-written
  **"How it's priced" / "Market data" / "Limitations"** lines in
  `docs/pricing/trades/TRADE_*.md` (the "This trade" tab), then **re-run the pricer at
  2026-07-10 and `python scripts/pricer/gen_trade_docs.py`** to refresh the auto-generated
  economics + live-diagnostics tables. The generator preserves prose and only refreshes
  numbers, so both steps are needed. *This step was added 2026-07-12 after the per-trade
  pages were found stale post-A1..B1 — do not skip it per item.*
- **Governance** — the engine/assumption/limitation strings in
  [`configs/pricer/model_inventory.json`](../../configs/pricer/model_inventory.json).

Golden-PV note: any item that changes a PV must rebaseline
`configs/pricer/golden_pvs.json` (and the per-asset `GOLDEN_PV` constants) with a
before/after diff recorded in the item's commit, and pass `/verify`.

---

## Tier A — high materiality, closeable with data already on disk

### A1 — Extend the SOFR curve beyond 10Y
**Trades:** #15–#19 (corp bonds, esp. ORCL 2053 / META 2054), #23 (5Y×10Y swaption underlying).
**Problem:** `rates.par_tenors` caps at 10Y; everything longer is flat-extrapolated even
though each Pensford snapshot carries 241 monthly points to ~2046 (~20Y).
**Change:**
- Add `12Y, 15Y, 20Y` to `rates.par_tenors` (real data; no extrapolation).
- Long-end (25–30Y for ORCL/META): switch the curve's beyond-last-node behaviour from
  DF-flat (`np.interp` clamp in `rates_adapter._df_at`, and QL DF-flat) to **constant
  instantaneous-forward extrapolation** off the 20Y node — either by (a) synthesising a
  30Y par node by holding the last forward flat, or (b) grafting the Treasury par
  curve's 20Y→30Y forward *shape* onto SOFR. Prefer (a) for curve-consistency; document
  the choice.
**Files:** `configs/pricer/pricer_config.json` (`rates.par_tenors`),
`src/pricer/loader/rates_adapter.py` (long-end node synthesis), possibly
`src/pricer/ql/common/curve_builder.py` (extrapolation mode).
**UI:** `CurveChart` already renders par/zero/DF/forward — the new nodes appear
automatically; add the long-end extrapolation note to `01-sofr-multicurve.md`.
**Acceptance:** ORCL/META PVs move (record delta); par OIS reprices at every new node;
`test_curve_builder` extended for 15Y/20Y; golden PVs rebaselined; `/verify` green.

### A2 — Bootstrap a multi-tenor hazard curve for single-name CDS
**Trades:** #28 (ORCL — has 1/2/3/4/5/10Y quotes), #29 (META — thin, stays flat fallback).
**Problem:** `credit_adapter` hard-filters to the 5Y bucket and `cds_pricer.calibrate_flat_hazard`
fits one hazard. Term structure of default risk and curve-shaped CS01 are lost.
**Change:** widen the credit adapter to return a **term structure of par spreads** (per
available tenor, notional-weighted median), and add a `calibrate_piecewise_hazard`
(sequential secant bootstrap of a `ql.PiecewiseFlatHazardRate` / survival curve) with a
graceful fall-back to flat when <2 clean tenors. Keep the flat path for META/CDX.
**Files:** `src/pricer/loader/credit_adapter.py`, `src/pricer/ql/credit/cds_pricer.py`,
`configs/pricer/pricer_config.json` (`credit.*` tenor grid).
**UI:** `_explain_cds` already emits a survival table — extend to multi-tenor hazard
rows; add a **hazard-curve line chart** to `CalculationLineage.tsx`.
**Acceptance:** ORCL CDS prices off a bootstrapped curve; per-tenor CS01; flat fallback
proven for thin names; golden rebaselined; `/verify` green.

### A3 — Term-structured / CDS-implied corporate-bond discounting
**Trades:** #15–#19. **Depends on A2.**
**Problem:** corp bonds discount on SOFR + a single flat Z-spread; CDS→bond basis = 0.
**Change:** discount corp cashflows on `SOFR + issuer survival/hazard curve` (or, minimally,
a **term structure of Z-spreads**) so the bond is consistent with the issuer CDS curve.
Interpolate the SOFR-vs-Treasury basis along maturity instead of one 10Y point.
**Files:** `src/pricer/ql/bonds/bond_pricer.py`, `src/pricer/loader/bonds_adapter.py`,
`src/pricer/ql/common/curve_builder.py` (spreaded/hazard-discount curve).
**UI:** bond `_explain` shows the spread term structure + basis; `20-bonds.md` update.
**Acceptance:** long bonds reflect curve shape; bond CS01 becomes key-rate; golden
rebaselined; `/verify` green.

---

## Tier B — medium materiality, data available

### B1 — Discrete dividends for American calls
**Trades:** #7 (AAPL LEAPS), #9 (MSFT), #10 (META), #14 (QQQ).
**Problem:** continuous `q` only; American-call early exercise is driven by discrete
ex-div. `data/marketdata/equity/dividends.csv` is unused by the option path.
**Change:** feed discrete dividend dates/amounts to an escrowed-dividend /
`ql.DividendVanillaOption` FD engine for dividend-paying American names; keep continuous
`q` for non-payers.
**Files:** `src/pricer/ql/equity/option_pricer.py`, `src/pricer/loader/equity_adapter.py`.
**UI:** option `_explain` lists the dividend schedule used; `11-equity-options.md` update.

### B2 — Real `BlackVarianceSurface` to the American FD engine
**Trades:** #7–#11, #14.
**Problem:** the good `EquityVolSurface` is collapsed to a flat `BlackConstantVol` per leg
for FD legs, so local skew across the exercise region is invisible.
**Change:** build one `ql.BlackVarianceSurface` per underlier from the existing IV grid
and hand it to the FD engine; keep the analytic per-point vol for Europeans.
**Files:** `src/pricer/ql/equity/option_pricer.py`, `src/pricer/ql/common/vol_builder.py`.
**UI:** **vol-surface heatmap** (strike×expiry) on the option Explain screen via `Heatmap`.

### B3 — Real foreign curves for FX
**Trades:** #26 (USD/TWD), #27 (USD/JPY).
**Problem:** JPY = single flat TONAR rate; TWD = hardcoded 2.00% constant;
`cbc_twd_ndf_fix.csv` unused; NDF spot uses Fed H.10 not the CBC fixing.
**Change:** bootstrap a JPY OIS strip from TONAR history; build a TWD forward/discount
curve from CBC NDF points; use the CBC NDF fixing for TWD spot. (XCCY basis stays a
documented gap — see C2.)
**Files:** `src/pricer/loader/fx_adapter.py`, `src/pricer/ql/fx/fx_pricer.py`,
`configs/pricer/pricer_config.json` (`fx.*`).
**UI:** FX `_explain` shows the foreign-curve nodes; `40-fx.md` update.

### B4 — Index-CDS mechanics for CDX.NA.IG
**Trade:** #30 ($200mm).
**Problem:** priced as a single-name flat-hazard proxy; no index factor / fixed-coupon
upfront mechanics.
**Change:** apply the index factor (defaulted-name adjustment) and standard fixed-100bp
coupon upfront conversion; document residual constituent-diversification error.
**Files:** `src/pricer/ql/credit/cds_pricer.py`, `src/pricer/loader/credit_adapter.py`.
**UI:** CDS `_explain` shows index factor + upfront; `50-credit-cds.md` update.

### B5 — Recovery sensitivity (rec01) + widen credit filter
**Trades:** #28–#30. **Change:** add a recovery bump measure; widen the 5Y-only tenor
filter to consume 3/7/10Y. **Files:** `cds_pricer.py`, `credit_adapter.py`.

---

## Tier C — data-blocked or low ROI (document / partial)

- **C1 — Rates vol surface / cube** (#23/#24/#25 + Bermudan calibration target). Full
  smile+cube needs **licensed** data (DTCC SDR IR-options not cleanly calibratable —
  ~20/540 usable rows, no option type). Deliverable: a **term-structure-only** normal-vol
  proxy (expiry-dependent MOVE anchoring) so the Bermudan co-terminal calibration is no
  longer fed one flat number, plus a governed write-up of the smile gap.
- **C2 — XCCY basis** (#26/#27). No free TWD / xccy-basis source. Document as a governed
  limitation; expose a basis input hook (default 0) so a licensed basis drops in.
- **C3 — VIX term complex** to anchor equity-vol term structure — marginal (chains carry
  the surface). Document as risk-only.
- **C4 — SOFR fixing history** (`nyfed_sofr_rates`) wired into `IndexManager` for
  seasoned-OIS accrual. Latent (all trades spot-start today).
- **C5 — DTCC cleared-swap & FINRA UST cross-checks** — DQ gates, not PV.
- **C6 — Callable / OAS framework** — no callable in the book; add
  `CallableFixedRateBond` + `TreeCallableFixedRateBondEngine` when one enters.

---

## Sequencing

A1 → A2 → A3 (A3 depends on A2) → B1 → B2 → B3 → B4 → B5 → C1 (partial) → C2–C6 (docs).
Each lands independently. UI + docs + golden rebaseline + `/verify` per item.
