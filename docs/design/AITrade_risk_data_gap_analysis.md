# AITrade Risk Module — Market-Data Gap Analysis

Author: Risk/data review (Claude)
Date: 2026-06-21
Scope: Inventory every calculation in the risk module (`src/pricer/risk/`), map each to its
required market data, cross-reference against what is present in `data/marketdata/`, and
outline the gaps systematically, then resolve them with **free data-source alternatives** + risk
methodology. **Workstream complete — see §0 for the closeout** (§5 holds the per-gap source table).

> Companion to the scenariogen stress-test design (`AITrade_stresstest_scenario_def_design.md`),
> whose S0 covariance + full-reval VaR consume the **same** `FactorHistory` series analyzed
> here — so every gap below also constrains the stress engine (notably the §11.7 inflated-beta
> issue, which is GAP 2).

---

## 0. Workstream closeout (2026-06-21)

The gap workstream is **complete**: every gap is either fully resolved or has a clearly-documented
residual that is genuinely unfillable for free (needs a licensed feed or a disproportionate build).
The sensitivity VaR went from a **4-factor** model to a **39-factor** one; the full-reval VaR /
scenariogen covariance now run on real per-name equity history, an 11-node IR curve, clean credit,
a JPY foreign-rate factor, and an equity vol term/skew surface. All changes are free-sourced,
verified (ruff + mypy clean, full pytest green), and committed atomically per gap.

| Gap | Status | What shipped | Remaining (no free data / out of scope) |
|---|:--:|---|---|
| GAP 1 — clean credit | ✅ | BofA IG OAS into both VaR engines | per-issuer (ORCL/META) + pre-2023 OAS |
| GAP 2 — option-underlier spot | ✅ | 6 names via Yahoo; real per-name betas | — |
| GAP 3 — IR curve tenors | ✅ | 11-node grid + key-rate VaR | Treasury≈SOFR basis approximation |
| GAP 4 — equity vol | ✅/◐ | per-name vega + **term/skew surface** | per-name vol *level* (no published index) |
| GAP 5 — FX foreign-rate/basis | ◐ | **JPY rho** from TONAR | TWD-rate + **USD XCCY basis** (no free data) |
| GAP 6 — rates-vol surface | ◐ | **term rates-vega** + scenario-shock | term-vol *history* + strike surface (no free data) |
| GAP 7 — coarse sens-VaR | ✅ | keyed multi-factor model | — |

**The only remaining *implementable* item** is GAP 5's **XCCY basis** (add an `FxData` basis field +
FX-pricer term + a basis bump/sensitivity), but it has **no free calibration data**, so it would be
plumbing driven by a placeholder — deferred until a basis feed is licensed. Everything else left is
pure sourcing (licensed data) or a disproportionate pricer/data-engineering build.

### Addendum (2026-07-11) — SOFR discount-curve completeness

A separate, higher-impact defect than any factor-history gap: the committed
`data/marketdata/rates/pensford_sofr_forward.csv` held **one forward point per snapshot** (the
archive-Excel downloader collapses the monthly path via `upsert_csv(date_col="as_of_date")`). With
a single point the OIS builder can't compound a path, so the **SOFR discount curve degenerated to
~0%** — every swap/bond/CDS discounted at zero, and the sensitivity/scenario/VaR numbers that read
those PVs inherited the error. **Fixed** by ingesting the full monthly path from Pensford's live
Supabase source (`scripts/rebuild_pensford_from_supabase.py`); the curve is now a real ~4%
(1M 3.68% → 10Y 4.14% @ 2026-07-10). Effect: 2026-05-29 book PV $1.62bn → $1.44bn (bonds near par;
rates −$1.1mm → +$7.1mm). Default valuation date moved to **2026-07-10** (full book incl. credit
prices there). See [`01-sofr-multicurve.md`](../pricing/methodology/01-sofr-multicurve.md) and the
[market-data guide](../guides/marketdata_guide.md). The archive downloader's collapse bug is
**fixed** (de-dups on `(as_of_date, settlement_date)`); the rebuild script remains the way to pull
the *current* curve, since the Pensford archive only publishes month-end files (lagging).

---

## 1. Risk-calculation inventory & data-dependency class

The risk module has five calculation types, in two data-dependency classes:

| Calculation | File | Method | Data dependency |
|---|---|---|---|
| **Sensitivities / Greeks** (DV01, key-rate, CS01, vega, Δ/Γ, FX Δ) | `sensitivities.py` | central bump-and-revalue | **Reuses the pricer's resolved market data** — no history |
| **Stress scenarios** | `scenarios.py` | full revaluation | Reuses pricing data + shock sizes — no history |
| **P&L explain / attribution** | `pnl_explain.py` | Greeks × shocks vs full reval | Reuses sensitivities — no history |
| **Sensitivity-based VaR/ES** | `var.py` | historical simulation on Greeks | **Needs historical factor-move series** |
| **Full-revaluation VaR/ES** | `historical_var.py` + `factor_history.py` | replay daily moves, reprice | **Needs aligned daily history for 6 factor groups** |

**Key conclusion:** Greeks, scenarios, and P&L-explain have **no market-data gap** — they reuse
the pricing inputs, and all trades price. (The CDS `IsdaCdsEngine` requires the discount-curve
reference date to equal the eval date — an engine constraint at 2026-06-17, **not** missing data.)
**All gaps are in the historical factor series** that feed the two VaR engines (and the
scenariogen covariance, via the shared `FactorHistory`).

---

## 2. Market-data availability matrix (historical factor series)

| # | Factor group | Series needed | File(s) | Status |
|---|---|---|---|---|
| 1 | IR curve | UST/SOFR nodal daily moves | `rates/fred_rates.csv` | ⚠️ only DGS2/5/10/30 (4 CMT nodes) |
| 2 | Equity spot — cash book | NVDA, TSM, AVGO, MU, SMH, XLK | `equity/prices_daily.csv` | ✅ present |
| 3 | Equity spot — option underliers | AAPL, MSFT, META, TSLA, QQQ, SPX | `equity/prices_daily.csv` | ❌ absent (only NVDA overlaps) |
| 4 | Equity vol — systematic | VIX | `options/vol_indices/VIX_History.csv` | ✅ present |
| 5 | Equity vol — per-name / surface | per underlier; term/skew | `options/vol_indices/*` | ⚠️ only VXAPL/VXAZN/VXGOG; term/skew unused |
| 6 | Rates vol | MOVE | `stress/move_index.csv` | ✅ present |
| 7 | FX spot | USD/JPY, USD/TWD | `fx/ecb_fx_rates.csv`, `fx/fed_h10_fx_rates.csv` | ✅ present |
| 8 | FX foreign rates / XCCY basis | TWD/JPY curves, basis | — | ❌ absent (basis held at 0) |
| 9 | Credit spread | IG/HY OAS or issuer spreads | proxies in use; `stress/fred_stress_macro.csv` | ⚠️ clean series present but **unused** |

**Portfolio universe (for reference)** — the 30 trades **as analyzed (2026-06-21)**; the live book has
since grown to **38** (see [`portfolio_rebalance_review.md`](portfolio_rebalance_review.md) — core
default, +8 trades):
- equity (stock ×6): NVDA, AVGO, MU, TSM, SMH, XLK → all have history.
- options (equity_option ×8): AAPL, META, MSFT, NVDA, QQQ, SPX×2, TSLA → only NVDA has spot history.
- bonds (ust_bond ×2; corp_bond ×5: AAPL, AMZN, META, MSFT, ORCL).
- credit (cds ×3: CDX.NA.IG, META, ORCL).
- rates (ois_swap, european_swaption, bermudan_swaption, cap).
- fx (fx_forward = JPY, xccy_swap = TWD).

---

## 3. Gaps, systematically (prioritized)

### GAP 1 — Clean credit series exists but is NOT wired in *(highest value, lowest effort)* — ✅ RESOLVED 2026-06-21
- **Calcs affected:** sensitivity VaR `CREDIT`→`CS01`; full-reval VaR credit factor.
- **Was:** sensitivity VaR proxied credit from **MOVE** level changes (`0.5 bp/pt`); full-reval
  used the **DTCC CDX daily median** (`credit/dtcc_index_cds.csv`), noisy enough to need a ±25 bp cap.
- **Fix applied:** both engines now use the clean **`BAMLC0A0CM` (ICE-BofA US Corporate IG OAS)**
  from `stress/fred_stress_macro.csv` (784 daily points, realistic ±1–2 bp moves):
  - sensitivity VaR: `config['risk']['var']['factors']['CREDIT']` → BofA IG OAS (`move_unit_bp`).
  - full-reval VaR: `FactorHistory._credit` prefers `_credit_oas` (config `hist_var.credit_file`/
    `credit_column`), with the capped DTCC median kept only as a fallback (`_credit_dtcc`).
  - regression tests lock the wiring (`test_credit_factor_uses_clean_oas`,
    `test_var_credit_config_points_to_baml`).
- **Residual data gap (still open):** BAML OAS populated only ~2023→present (blank 2018–2022) →
  unusable for crisis-window covariance; index-level only → **no per-issuer (ORCL/META) spread
  history** (`credit/dtcc_single_name_cds.csv` is sparse/noisy). Tracked in §5.

### GAP 2 — Per-name equity spot history for 6 option underliers — ✅ RESOLVED 2026-06-21
- **Names:** AAPL, MSFT, META, TSLA, QQQ, SPX (NVDA was already covered).
- **Was:** no daily spot history → full-reval VaR / scenariogen covariance / sensitivity VaR used
  the **XLK systematic proxy (β=1)** for them.
- **Fix — source:** fetched ~2,127 daily bars each (2018→2026) from **Yahoo Finance** (keyless
  chart API; Stooq now serves anti-bot HTML) via `scripts/marketdata/fetch_option_underliers.py` —
  an **additive** upsert that only adds the new tickers, leaving the cash-book rows +
  `dividends.csv`/`splits.csv` untouched (pricing inputs stable). Added the 6 to
  `configs/marketdata/equity/equity_marketdata.json`.
- **Fix — wiring:** `factor_history._EQUITY_TICKERS` now includes the 6 (covariance + full-reval use
  real per-name returns); the VaR config `EQ_<name>` factors point to their own tickers (XLK proxy
  removed). Verified **distinct, realistic betas** (given TSM −10%: TSLA −6.4%, QQQ −4.5%, META
  −3.2%, SPX −2.8%, MSFT −1.5%, AAPL −0.9% — vs a uniform XLK proxy before).
- **Governance:** `prices_daily.csv` content changed → snapshot_id **re-baselined**
  (`56a863cf…`→`680bb0b1…`) in `golden_pvs.json`; all 30 PVs verified unchanged.
- **Type:** sourcing (done, free). Note: the §11.7 inflated-MU-beta item is separate (MU is a
  cash-book name that always had history; covariance shrinkage is the follow-up there).

### GAP 3 — IR curve history granularity — ✅ RESOLVED 2026-06-21
- **Calcs affected:** full-reval VaR curve-shape shock, covariance IR nodes, **sensitivity-VaR
  key-rate** (the GAP 7-IR residual).
- **Was:** 4 CMT nodes only (DGS2/5/10/30); sensitivity VaR was IR-parallel (DGS10) only.
- **Fix — source (free):** re-pulled `fred_rates.csv` via the keyless fredgraph endpoint with 7
  added tenors → **11-node grid** DGS1MO/3MO/6MO/1/2/3/5/7/10/20/30
  (`configs/marketdata/rates/rates_marketdata.json`). `fred_rates.csv` is **risk-history only**
  (no pricer reads it) → **no snapshot/golden re-baseline needed**.
- **Fix — wiring:** `factor_history._IR_TENOR_YEARS` + `scenariogen.factor_keys.IR_TENOR_YEARS`
  expanded to the 11-node grid (curve-shape shocks now resolve steepeners/flatteners/butterflies,
  not a quasi-parallel 4-node move); the sensitivity VaR is now **key-rate** —
  `_book_sensitivities(key_rates=True)` and config maps `IR_KR:<tenor>`→`DGS<tenor>` (12 buckets;
  9M proxied to DGS1, no DGS9MO). Verified: 12 IR key-rate factors contribute; VaR99 went
  $7.85mm→$9.18mm (curve-shape risk previously missed).
- **Residual:** history is Treasury CMT used to shock the SOFR/OIS curve (Treasury≈SOFR basis
  approximation — a genuine SOFR nodal history would remove it).
- **Type:** pull-scope (done, free) + wiring.

### GAP 4 — Per-name & surface equity-vol history — ◐ MOSTLY RESOLVED 2026-06-21 (surface ✅; per-name level sourcing open)
- **Calcs affected:** vega VaR equity-vol factor.
- **Was:** a single systematic **VIX** factor for the whole equity-option book's vega.
- **Fix applied:** the sensitivity VaR now keys vega per underlier (`VEGA_EQUITY:<name>`, same
  mechanism as GAP 7's delta keying); **AAPL uses its own `VXAPL` index**, the other option
  underliers proxy to VIX. Verified: 7 `VEGA_EQUITY:<name>` keys, AAPL sourced from VXAPL.
- **Re-scoped on inspection (the doc's original "3 per-name indices" was optimistic):**
  - of the three per-name indices, only **VXAPL→AAPL** maps to a book option position;
    **VXAZN (AMZN)** and **VXGOG (GOOG)** correspond to no option position → not wireable.
  - CBOE publishes no per-name index for NVDA/MSFT/META/TSLA → still VIX-proxied (**sourcing gap**).
- **Vol-surface risk model — ✅ RESOLVED 2026-06-21 (methodology):** the engine had only a parallel
  IV bump; added two parametric surface factors using the in-repo (previously unused) indices:
  - **term** (`bumps.shift_equity_vol_term`): tilt IVs by `g(t)=(√t−√t₃₀)/(√t₉₀−√t₃₀)`, driven by
    daily Δ(VIX3M−VIX); sensitivity `VEGA_EQ_TERM`; VaR factor `EQVOL_TERM` (derived spread via the
    new `_factor_moves` minus-column support); `factor_history` `eqvol_term`.
  - **skew** (`bumps.shift_equity_vol_skew`): tilt IVs by `−ln(K/S)`, driven by ΔSKEW × a documented
    `skew_vol_per_point` coefficient (the one modeling knob — no historical surfaces to calibrate it
    tighter); sensitivity `VEGA_EQ_SKEW`; VaR factor `EQVOL_SKEW`; `factor_history` `eqvol_skew`.
  - wired into sensitivities → sensitivity-VaR → full-reval VaR → scenariogen (`factor_keys` +
    `factor_vector`). Verified: book term-vega ≈ $0.77mm; 38 VaR factors; VaR99 $9.18mm→$11.1mm.
- **Residual:** per-name vol *level* for non-published names (NVDA/MSFT/META/TSLA) still VIX-proxied
  (sourcing); the skew coefficient is a parametric assumption pending licensed surface history.
- **Type:** vol-surface methodology ✅ done; residual per-name vol = sourcing.

### GAP 5 — FX foreign-rate / XCCY-basis history — ◐ PARTIALLY RESOLVED 2026-06-21
- **Calcs affected:** FX VaR; foreign-rate rho; `xccy_swap` (#26) basis risk.
- **Was:** FX risk was **spot-only** — the engine had **no bump for `FxData.foreign_rate`** at all,
  so FX rho was invisible; FX VaR was JPY-spot only; basis held at 0.
- **Sourcing result (free):** **JPY foreign rate = `boj_tonar.csv`** (daily BoJ TONAR, already in
  the repo, free) — the one usable free daily foreign-rate series. **TWD foreign rate** has no free
  daily curve (FRED's Taiwan discount rate didn't serve CSV; design holds TWD at CBC policy
  constant). **USD XCCY basis** has **no free daily source** (Bloomberg/Markit proprietary).
- **Fix applied:** added a foreign-rate (FX **rho**) factor end-to-end:
  - `bumps.shift_fx_rate` / `shift_fx_rate_named`; `sensitivities` emits **`FX_RATE`** (per +1bp);
    VaR keys it per currency (`FX_RATE:<ccy>`); config `FXRATE_JPY` maps TONAR daily change.
  - `factor_history` adds `fxrate:JPY` (full-reval VaR + scenariogen covariance);
    `scenariogen.factor_keys` + `factor_vector` handle the `fxrate:<ccy>` factor.
  - Verified: rho now measured (TWD xccy-swap rho ≈ $18.6k/bp, JPY fwd ≈ $1.3k/bp); JPY rho VaR fed
    from TONAR.
- **Residual (no free source):** **TWD foreign-rate** rho is *measured* but absent from VaR (no TWD
  rate history; held constant); **USD XCCY basis** still unmodelled (no free daily data + would need
  an `FxData` basis field + FX-pricer change). These are the genuinely unfillable-for-free pieces.
- **Type:** foreign-rate done (free, JPY); TWD-rate + XCCY basis remain (no free source).

### GAP 6 — Rates-vol is a single point (MOVE) — ◐ PARTIALLY RESOLVED 2026-06-21 (sensitivity ✅; VaR history blocked)
- **Calcs affected:** swaption/cap vega (3 trades #23–25).
- **Was:** one MOVE-driven **level** factor; no term/expiry structure; the term-structure rates-vega
  was invisible.
- **Sourcing result:** **no free rates-vol surface/term-structure history** — MOVE is a single
  (1M) level index; `rates/dtcc_sdr_ir_options.csv` (90k rows) has ambiguous premium units (many 0)
  and would need per-date historical forwards to imply Bachelier vols → a large, low-quality
  data-engineering effort. Like the XCCY basis, the term-vol *history* is unfillable for free.
- **Fix applied (methodology, no pricer rewrite):** `bumps.shift_rates_vol_term` tilts the flat
  normal vol by `g(t)=(√t−√t₁)/(√t₅−√t₁)` using each instrument's own expiry
  (`FlatNormalVolData.tenor`), so the **term-structure rates-vega is now measured** —
  sensitivity `VEGA_RATES_TERM`. Verified it differentiates by expiry (5Y swaption full, 2Y
  Bermudan ≈⅓, 1Y cap ≈0 at the anchor). Also wired into scenariogen
  (`factor_keys.ratesvol_term` + `factor_vector`) so a stress scenario can **shock** the rates-vol
  term slope as a driver/overlay.
- **Residual:** the term factor has **no VaR/covariance contribution** (no free term-vol history to
  drive it — measured only, like the TWD rho); a full strike-smile surface would need a pricer-level
  vol-surface model + licensed swaption-vol data (disproportionate for a 3-trade book).
- **Type:** sensitivity + scenario-shock ✅ done; VaR-history + strike surface remain (no free data).

### GAP 7 — Sensitivity-VaR factor model is coarse *(methodology/config; overlaps 1–5)* — ✅ RESOLVED 2026-06-21
- **Was:** the whole equity book's `EQUITY_DELTA` mapped to **NVDA returns**; all credit to MOVE
  (fixed in GAP 1); FX to **JPY only**; IR to DGS10 parallel; **no vega factors at all** (4-factor model).
- **Fix applied:** the sensitivity VaR is now a genuine **multi-factor model** — `VarEngine`
  keys book sensitivities by exposure (`EQUITY_DELTA:<ticker>`, `FX_DELTA:<ccy>` via
  `_exposure_keys`, inspecting each trade's resolved market data), and the config maps a
  per-name / per-currency history series to each:
  - **equity per-name** for all 13 underliers (cash book NVDA/TSM/AVGO/MU/SMH/XLK on their own
    history; option underliers AAPL/MSFT/META/TSLA/QQQ/SPX on the **XLK proxy** pending GAP 2);
  - **FX per-currency** JPY + TWD (**TWD was previously absent** from VaR entirely);
  - **vega factors added**: `VEGA_RATES` (MOVE) + `VEGA_EQUITY` (VIX) — previously missing.
  - Verified: 18 contributing factors (was 4); VaR99 ≈ $7.7mm (properly diversified).
- **Residual:** ~~IR parallel-only~~ ✅ now key-rate (GAP 3); ~~option-underlier equity XLK proxy~~
  ✅ now own history (GAP 2). The GAP 7 residuals are closed.

---

## 4. Remediation classes

| Class | Gaps | Effort | Needs new data? |
|---|---|---|---|
| **Wire-up only** (data already in repo) | ~~GAP 1 (BAML OAS)~~ ✅, ~~GAP 7 (multi-factor config)~~ ✅, ~~GAP 4 (VXAPL→AAPL)~~ ◐ done where applicable | low | no |
| **Pull-scope** (same free FRED feed) | ~~GAP 3 (more DGS tenors + key-rate VaR)~~ ✅ | low | no (re-pull) |
| **Genuine sourcing** (the later investigation) | ~~GAP 2~~ ✅ (Yahoo), ~~GAP 5 FX rho~~ ◐ (TONAR), GAP 1 residual, GAP 5 TWD-rate/basis (no free src), GAP 4 residual, GAP 6 | medium–high | yes |

---

## 5. Candidate free data sources *(to be filled in the follow-up investigation)*

| Gap | Missing series | Candidate free source | Notes / license | Status |
|---|---|---|---|---|
| GAP 1 residual | Pre-2023 IG/HY OAS; per-issuer spreads | _TBD_ | | open |
| GAP 2 | AAPL/MSFT/META/TSLA/QQQ/SPX daily spot | **Yahoo Finance chart API** (keyless) | free; split-adjusted close; `scripts/marketdata/fetch_option_underliers.py` | ✅ done |
| GAP 3 | DGS1MO/3MO/6MO/1/3/7/20 | **FRED fredgraph** (keyless) | free re-pull; enabled key-rate VaR | ✅ done |
| GAP 4 | vol-surface term/skew dynamics | **VIX3M + CBOE SKEW** (in repo) | free; parametric surface-risk model (term clean, skew has 1 documented coeff) | ✅ done |
| GAP 4 | per-name equity vol level (NVDA/MSFT/META/TSLA) | _none free (no published index)_ | proprietary | open |
| GAP 5 | JPY foreign rate | **BoJ TONAR** (`boj_tonar.csv`, in repo) | free, daily | ✅ done (FX rho) |
| GAP 5 | TWD foreign-rate curve | _none free (CBC policy held constant)_ | proprietary/low-freq | open |
| GAP 5 | USD XCCY basis (JPY, TWD) | _none free (Bloomberg/Markit)_ | proprietary | open |
| GAP 6 | IR normal-vol term sensitivity | **per-expiry parametric tilt** (in-engine) | term rates-vega measured + scenario-shockable | ◐ done |
| GAP 6 | IR normal-vol surface *history* | _none free (MOVE level only; SDR premiums ambiguous)_ | proprietary / large SDR pipeline | open |

---

*Bottom line: the bump-and-revalue calculations (Greeks, scenarios, P&L-explain) are fully
data-supported. The VaR engines run on a thin historical-factor set with three quick wins
available today (wire in BAML OAS, the term/skew + 3 per-name vol indices, and broaden the
sensitivity-VaR config) and a clear sourcing backlog (per-name equity spot, pre-2023 OAS +
per-issuer spreads, FX curves/basis, IR vol surface) for the follow-up.*
