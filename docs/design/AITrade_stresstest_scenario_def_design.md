# AI-Themed Portfolio — Stress-Testing Scenario Definition: Design & High-Level Plan

Author: Architecture/quant design (Claude)
Date: 2026-06-20
Scope: How to define **formal, internally-consistent (correlated) stress scenarios** for the
38-trade AI-themed derivatives book, what **inputs** are required to build them, a **generic
scenario catalog** (incl. Taiwan geopolitical, AI bust, rate/capital, key-man), and the **plan**
to wire them into the existing AITrade valuation/risk engine and the MacroRAG narrative layer.

> This is a design + plan, not an implementation. It builds on what already exists:
> `src/pricer/risk/` (`scenarios.py`, `bumps.py`, `factor_history.py`, `var.py`,
> `historical_var.py`) and the MacroRAG source catalog
> (MacroRAG repo, `docs/design/Macro_RAG_Source_Catalog.md`).

---

## 1. The problem, precisely

We must value the book under **plausible future shock scenarios** and report the P&L **and the
change in required capital** (VaR/ES). A scenario is only useful if it is:

1. **Complete** — it shocks *every* risk factor the book is exposed to (a partial shock
   mis-states P&L: e.g., a Taiwan shock that moves TWD but not semis vol or credit is wrong).
2. **Internally consistent / correlated** — the factor moves co-vary the way real markets do
   (in a risk-off, equities fall *as* vol spikes *as* credit widens *as* rates rally to quality).
   Hand-picking each leg independently produces incoherent, non-credible scenarios.
3. **Narrative-anchored & plausible** — tied to a real catalyst with a defensible severity
   (this is where MacroRAG comes in: *what* fires the scenario and *how big*).
4. **Governed & reproducible** — versioned, signed-off, and pinned to the data/code that
   produced the number (consistent with the AITrade provenance/golden-PV discipline).

The four headline scenarios the business wants — **Taiwan geopolitical, AI bust, interest-rate
impact on capital, key-man (TSLA/NVDA)** — are precisely Cembalest's four medium-term risks
(power, China-standalone, Taiwan, hyperscaler-profit "metaverse moment") plus the rate channel,
which the MacroRAG catalog already frames as "a specification for your stress scenarios."

---

## 2. The book's risk-factor universe (what must be shocked)

Every scenario is a vector over these factors. Each maps to an existing bump transform in
`src/pricer/risk/bumps.py`, so the valuation engine can already *apply* any vector — the work is
*defining* coherent vectors.

| # | Risk factor | Granularity | Bump transform (exists) | Trades touched |
|---|---|---|---|---|
| 1 | **IR curve** (SOFR/UST par nodes) | per-tenor (curve shape) | `shift_ir_curve` / `shift_ir` | rates #22–25, bonds #15–21, FX #26–27, all equity discounting |
| 2 | **Credit spread** | systematic IG + per-issuer (ORCL/META) + CDX | `shift_credit` (today uniform) | bonds #15–19, CDS #28–30 |
| 3 | **Equity spot** | **per-name** (NVDA, TSM, AVGO, MU, AAPL, MSFT, META, TSLA, AMZN, GOOGL, AMD + XLK/SMH/SPX/QQQ) | `scale_equity_named` / `scale_equity` | cash #1–6, options #7–14 |
| 4 | **Equity vol (IV)** | systematic (VIX) + per-name (VXAPL…) | `shift_equity_vol` | options #7–14 (vega) |
| 5 | **Rates vol** (normal/Bachelier) | systematic (MOVE) | `scale_rates_vol` / `shift_rates_vol` | swaptions/cap #23–25 |
| 6 | **FX spot** | per-pair (USD/TWD, USD/JPY) | `scale_fx_named` / `scale_fx` | FX #26–27, TSM-ADR FX beta |
| 7 | **Foreign rates / XCCY basis** | per-ccy (flat today; basis=0) | *(extend `FxData`)* | #26–27 |

**Known data constraints (inputs to improve — see §4):** option underliers AAPL/MSFT/META/TSLA/
QQQ/SPX have **no daily spot history** (only NVDA + the cash book), the IR curve history is only
DGS2/5/10/30, and the credit factor is a noisy CDX-median proxy. These limit covariance estimation
today and are flagged as data inputs to source.

---

## 3. What a "formal scenario definition" is

A scenario definition is **not** just a hand-set shock list. It is a small, governed object:

```
Scenario := {
  name, narrative, trigger/catalyst, severity tier, horizon,
  DRIVERS:    the 1–4 primary factor moves the narrative dictates (with magnitude + source),
  PROPAGATION: the method that fills in all OTHER factor moves consistently,
  OVERLAYS:    idiosyncratic single-name add-ons not in the systematic covariance (key-man),
  → DERIVED FULL SHOCK VECTOR over §2 (per-name, per-tenor),
  provenance:  data window + covariance id + macro source chunk-ids + scenario_version
}
```

The **drivers** are the few things the narrative actually asserts ("USD/TWD +8%, semis −25%").
The **propagation** step derives the co-moving rest of the vector from real market correlations so
the scenario is coherent. The **overlays** add what correlations *can't* capture (a CEO-departure
key-man drop is idiosyncratic by definition).

---

## 4. Inputs required to build the scenarios

This is the heart of the request. Six input classes, three of which already exist in-house:

### 4A. Risk-factor universe & exposures — *(have it)*
From the portfolio + pricer: the §2 factor list, and the book's **sensitivities** to each
(`RiskEngine.sensitivities` already gives DV01/key-rate, CS01, per-name delta/gamma, vega, FX
delta). Needed to know which factors matter and to sanity-rank scenario severity.

### 4B. Historical factor time series → covariance / correlation — *(mostly have it)*
The engine of *correlation*. `src/pricer/risk/factor_history.py` already date-aligns 6 factor
series (IR curve, per-name + systematic equity, VIX, MOVE, per-pair FX, CDX credit). Required:
- A **covariance/correlation matrix** Σ of daily factor moves over a chosen window.
- Choice of window/weighting: full-sample, **EWMA** (recent-weighted), or a **crisis-window**
  covariance (2020-03, 2022, 2018-Q4) for stress-realistic tails.
- **Gaps to fill (data inputs):** per-name spot history for the option underliers; a clean
  single-series IR curve (more tenors); a clean CDX/issuer spread series (replace the capped
  median proxy). Until sourced, use proxies (XLK for missing names) + document.

### 4C. Macro-narrative, policy & forward-looking inputs — **MacroRAG** *(have the design)*
The *direction, magnitude, trigger, and plausibility* of the **drivers** — the layer the numeric
pipeline cannot supply. Per the MacroRAG catalog, distilled into shock-vector parameters:
- **Rate/term-premium drivers:** FOMC/SEP/MPR + Treasury Refunding/TBAC + CBO (the rate leg size).
- **Credit/vol drivers:** Fed FSR + IMF GFSR + SLOOS (widening/valuation-stretch framing).
- **AI-capex/power drivers (the "AI bust" engine):** BlackRock BII + JPM EotM + EIA AEO/STEO +
  IEA + FERC interconnection (Methodology B "early bust" signal).
- **Taiwan/export-control triggers:** Federal Register (BIS-Commerce rules), Section 232,
  TSMC monthly sales, SIA, GPR/EPU indices (tail-probability weighting + the actual fire signal).
- **FX drivers:** BoJ Outlook (JPY), Treasury TIC, BIS funding (TWD/basis).
- MacroRAG emits a **proposed driver vector + cited justification** ("FOMC + Q3 refunding ⇒
  +75bp long-end, ΔMOVE +40, per [chunk_ids]"), which becomes the scenario's `DRIVERS`.

### 4D. Calibration anchors & reference templates — *(to assemble)*
To size severity defensibly:
- **Regulatory templates:** Fed **DFAST/CCAR** adverse / severely-adverse macro paths — a
  ready-made, supervisory-blessed shock-vector skeleton (the catalog calls this out explicitly).
- **Historical analogues:** COVID-2020, 2022 rates/credit, GFC-2008, 2018-Q4, 2015 CNY deval,
  2011 — each gives a real, fully-correlated move set to replay or anchor magnitude.
- **IMF GFSR / Fed FSR** narrative magnitudes for episodes whose free numeric history is gone
  (the documented pre-2023 OAS gap).

### 4E. Expert / management overlays — *(governance input)*
- **Severity tiers** per scenario (mild / adverse / severe), e.g. driver at the 95th/99th/99.9th
  historical percentile, or ×1/×2/×3 of a base move.
- **Single-name / key-man idiosyncratic** drops (NVDA −30%, TSLA −40%) and their **assumed
  spillover** to peers (the part not in systematic beta).
- **Recovery / liquidity** assumptions, holding horizon (1d / 10d / quarter), and any
  position-level carve-outs.

### 4F. The propagation model specification — *(to build)*
The rule that turns drivers → full coherent vector (§5). Inputs: Σ (4B), the driver set + sizes
(4C/4E), the severity tier, and the chosen method.

> **Summary of the answer:** to build a scenario you need (1) the factor universe + exposures,
> (2) a historical covariance of those factors, (3) MacroRAG driver direction/size/trigger,
> (4) calibration anchors (DFAST + historical episodes), (5) expert severity & key-man overlays,
> and (6) a propagation method. (1)–(2) exist in AITrade, (3) is the MacroRAG deliverable,
> (4)–(6) are the new build.

---

## 5. Methodology — making the shocks coherent & correlated

Four composable methods; the recommended production path is the **expert-anchored hybrid (E)**.

### A. Historical-analogue replay
Pick a crisis window; use its **actual** factor moves as the scenario vector. Maximally coherent
(it happened), zero modelling assumptions. Limitation: today's book/names differ from the episode;
needs mapping (e.g., GFC bank-equity moves → AI-name proxies). *Use for: backbone severity anchors.*

### B. Conditional-covariance propagation  *(the core correlation engine)*
Given driver moves `d` on a sub-set of factors, derive the **expected co-move of all other
factors** from the historical covariance (multivariate-normal conditional expectation):

```
x_rest = μ_rest + Σ(rest,drivers) · Σ(drivers,drivers)⁻¹ · (d − μ_drivers)
```

This yields a market-consistent full vector from a few narrative drivers — e.g., set TWD +8% and
semis −25%, and the method *derives* the consistent VIX spike, IG/HY widening, rate rally, and the
moves of every other equity name. Σ comes from `factor_history` (4B); use a **crisis-window or EWMA
Σ** for fat-tailed co-moves. Optionally scale the result to a severity tier. *This is the standard
"conditional / predictive" stress approach and is what makes scenarios correlated by construction.*

### C. Macro-factor model
Regress book factors on a few **macro drivers** (growth, rates, USD, risk-appetite, AI-capex) via
PCA or macro regressions; shock the macro drivers; propagate through the loadings. More
interpretable for narrative scenarios; needs the macro series (MacroRAG numeric + FRED). *Use for:
the "interest-rate impact on capital" and growth-driven scenarios.*

### D. Reverse stress testing
Search for the scenario (driver vector) that drives loss/capital past a threshold (e.g. ES > X).
Answers "what would break us?". Built on B/C + the engine's full-reval loop. *Use for: limit-setting.*

### E. **Expert-anchored hybrid (recommended)**
1. **MacroRAG + expert** set the **drivers** and **severity tier** (4C/4E) with citations.
2. **Conditional-covariance (B)** derives the **systematic co-moves** of all remaining factors —
   coherent by construction.
3. **Overlays (4E)** add **idiosyncratic** single-name moves (key-man) on top, with a partial
   peer-spillover from single-name correlations.
4. **Plausibility checks** (§9): the derived vector's joint move is within historical envelopes
   (or explicitly flagged as beyond-historical for "severe").
5. **Full revaluation** through `ScenarioEngine` → P&L; **VaR/ES under the shocked state** → capital.

**Key-man specifics:** a key-man event is idiosyncratic, so covariance alone misses it. Model it
as `idiosyncratic single-name drop + (its beta × any market move) + name vol spike + partial
peer spillover` (peers move by the historical single-name correlation × the idiosyncratic part).
For a *pure* key-man (no broad risk-off) the systematic legs are ~0 and only the name + spillover +
its IV move.

---

## 6. Generic scenario catalog

A standing set, each defined as drivers → (covariance-derived co-moves) + overlays, at 3 severity
tiers. The four named ones are the priority; the rest round out coverage.

| Scenario | Primary drivers (narrative-set) | Covariance-derived co-moves | Idiosyncratic overlays | MacroRAG trigger |
|---|---|---|---|---|
| **Taiwan geopolitical** | USD/TWD +8/15/25%; TSM −25/40/55% | semis (AVGO/MU/NVDA) ↓, equity vol ↑, IG/HY ↑, UST rally (flight-to-quality), USD/JPY ↑, XCCY basis widen | TSM-ADR gap-down; foundry-supply premium on AI names | Fed Register BIS rule / Section 232 / GPR spike |
| **AI bust ("metaverse moment" / capex bust)** | AI complex −30/45/60% (NVDA, hyperscalers); AI-capex run-rate ↓ | broad equity ↓, VIX ↑, IG/HY ↑ (financing-hump unwind), UST rally, MOVE ↑ | data-center power-demand collapse signal (Methodology B); hyperscaler-margin de-rate | BlackRock/JPM capex framing + EIA/FERC power |
| **Interest-rate / capital** | SOFR/UST +100bp parallel **and** +150bp 10s30s steepener (term premium) | credit modest ↑, equity ↓ (discount-rate), MOVE ↑, USD ↑ | long-end convexity on ORCL 2053 / META 2054 | FOMC/SEP + Treasury Refunding/TBAC + CBO |
| **Key-man (NVDA / TSLA)** | NVDA −30/40/50% **or** TSLA −35/45/55% (idiosyncratic) | small systematic risk-off (name beta only) | name IV spike; partial peer spillover via single-name corr; option-book gamma | (firm-specific; GPR/news, not macro) |
| Risk-off / financial-conditions tighten | VIX +15/25/40, NFCI ↑ | equities ↓, credit ↑, UST rally, USD ↑ | — | IMF GFSR + BofA FMS |
| Inflation surprise / Fed-on-hold | CPI surprise → rate path +50/100bp, cuts repriced out | curve bear-flatten, equity ↓, MOVE ↑ | — | BLS CPI + FOMC posture |
| Stagflation / power-cost shock | energy/inflation ↑ + growth ↓ | rates ↑ front, equity ↓, credit ↑ | power-cost margin hit to data centers | EIA + World Bank commodities |
| USD funding squeeze | XCCY basis −50/100bp, USD ↑ | FX ↓ vs USD, credit ↑ | #26/#27 basis leg | BIS Quarterly + OFR repo |
| Soft-landing / AI-capex boom *(upside)* | AI complex +20/35%, vol ↓ | equity ↑, credit tighten, rates ↑ modest | — | GS/BlackRock base case |
| Reverse stress (capital breach) | *solved* for ES > limit | derived | — | limit framework |

Each cell's magnitudes are **anchored** (4D) and the co-moves are **derived** (5B), not guessed.

---

## 7. Scenario definition schema (the formal artifact)

A versioned object (JSON), consistent with the MacroRAG §9B shock-vector handoff and the AITrade
provenance discipline. Drivers + propagation + overlays → a materialized full vector that the
engine applies; both the *definition* and the *materialized vector* are stored and versioned.

```jsonc
{
  "scenario_id": "taiwan_geopolitical",
  "version": "<content-hash>",                 // governed, like portfolio_version
  "narrative": "Taiwan crisis: export-control escalation + foundry disruption",
  "trigger": "BIS-Commerce rule / Section 232 / GPR-index spike",
  "severity": "adverse",                        // mild | adverse | severe
  "horizon_days": 10,
  "drivers": {                                  // narrative/MacroRAG-set, with sources
    "fx:USDTWD": {"move_pct": 8, "source": ["macro:fedreg:2026-..#chunk"]},
    "equity:TSM": {"move_pct": -25, "source": ["macro:tsmc:..", "house:JPM:.."]}
  },
  "propagation": {"method": "conditional_covariance",
                  "covariance": {"window": "crisis", "id": "<cov-hash>"}},
  "overlays": {                                 // idiosyncratic, not in covariance
    "equity:TSM": {"extra_pct": -10, "iv_pts": 15},
    "peer_spillover": {"basis": "single_name_corr", "fraction": 0.5}
  },
  "materialized": {                             // derived full vector over §2 (per-name/per-tenor)
    "ir_curve_bp": {"2Y": -30, "5Y": -38, "10Y": -42, "30Y": -40},
    "credit_bp": {"IG": 90, "ORCL": 140, "META": 95, "CDX": 100},
    "equity_pct": {"TSM": -35, "AVGO": -22, "MU": -20, "NVDA": -18, "AAPL": -9, "...": "..."},
    "equity_vol_pts": {"_systematic": 20, "TSM": 30},
    "rates_vol_rel": 0.30, "fx_pct": {"TWD": 8, "JPY": 6}
  },
  "provenance": {"data_window": "...", "macro_chunk_ids": ["..."], "approved_by": "...", "as_of": "..."}
}
```

---

## 8. Integration with the AITrade valuation/risk engine

The engine can already *apply* a full per-name/per-tenor vector (the P2 bump transforms). Required
extensions (incremental, on the existing `src/pricer/risk/`):

1. **Generalize `Scenario`** (`risk/scenarios.py`): today it carries scalar uniform shocks
   (`ir_bp`, `equity_pct`, …). Extend it to carry a **materialized per-factor vector** (per-name
   equity/vol, per-tenor IR, per-issuer credit, per-pair FX) and build the transform list from it
   (compose `scale_equity_named`, `shift_ir_curve`, `shift_credit` per-issuer, `scale_fx_named`,
   `shift_equity_vol` per-name). Keep the scalar form as the "uniform" special case.
2. **Scenario generator** (`risk/scenario_gen.py`, new): given a definition (drivers + method) and
   `factor_history`'s covariance, **materialize** the full vector (method 5B/E). Pure, testable.
3. **Covariance module** (`risk/covariance.py`, new): build Σ from `factor_history` with
   window/EWMA/crisis options; persist a covariance id for reproducibility.
4. **Capital output:** the existing `ScenarioEngine` gives full-reval **P&L**; add **ES/VaR under
   the shocked state** (re-run the risk engine on the shocked snapshot) → "interest-rate impact on
   **capital required**" becomes a first-class output.
5. **Governance:** version each scenario definition (content hash, like `portfolio_version`) and
   stamp `scenario_version` into the scenario report's provenance; add golden/plausibility checks.
6. **Service/CLI:** extend `risk_runner`/`/risk/scenario` to accept a scenario_id from the governed
   scenario store; output P&L by trade/asset-class + capital delta + the materialized vector used.

No change to the pricers — scenarios remain a market-data perturbation + full revaluation.

---

## 9. MacroRAG handoff, cadence & governance

- **Numeric/narrative split (per the catalog §9C):** numeric factor history stays in the AITrade
  Parquet store (covariance source); MacroRAG supplies the **driver direction/magnitude + trigger +
  citations** as the scenario `drivers` block.
- **Cadence:** scenario **definitions** refresh **quarterly** (post-refunding / post-AEO /
  post-earnings-capex); **materialized vectors** refresh **weekly** as covariance + driver reads
  update — matching the catalog's "definitions quarterly, vectors weekly."
- **GATE promotion:** hold a scenario out of the production run unless its MacroRAG source chunks
  pass freshness + citation-coverage (catalog §10) and its materialized vector passes plausibility.
- **Plausibility checks:** the joint move sits within historical envelopes for mild/adverse, or is
  explicitly flagged "beyond-historical" for severe; per-factor moves within sane bounds (reuse the
  data-quality bound idea); single-name overlays signed-off.
- **Reproducibility:** every scenario number pinned to `{scenario_version, covariance_id,
  market-data snapshot_id, portfolio_version, config_hash, git_sha}` — the AITrade provenance chain
  extended with the scenario + covariance ids.

---

## 10. High-level phased plan

> **Implementation note (2026-06-20):** scenario-generation code/config/tests live in a
> dedicated namespace — `src/scenariogen/`, `configs/scenariogen/`, `tests/scenariogen/` —
> rather than inside `src/pricer/risk/`. They reuse the pricer's bump transforms and the
> `factor_history` aligned panel (new public `FactorHistory.aligned_panel`). `src/scenariogen`
> is in the strict mypy gate.

| Phase | Deliverable | Builds on | Status |
|---|---|---|---|
| **S0 — Foundations** | `covariance.py` (Σ from `factor_history`, window/EWMA/crisis); fill the per-name equity-history + cleaner credit/IR data gaps (§4B) | P2 factor_history | **✅ done** — `scenariogen/covariance.py`: `FactorCovariance` (full/EWMA/crisis Σ, correlation, Gaussian `conditional_mean`, content-addressed `cov_id`); `aligned_panel` accessor. Data-gap sourcing (§4B) still open. |
| **S1 — Generalized scenario + generator** | Extend `Scenario` to a materialized per-factor vector; `scenario_gen.py` conditional-covariance propagation (5B/E); unit tests vs known co-moves | P2 bumps/scenarios | **✅ done** — `factor_keys.py` (canonical taxonomy), `factor_vector.py` (`MaterializedScenario` → bump transforms), `scenario_gen.py` (`ScenarioDefinition` + `ScenarioGenerator`: severity-scale → conditional-covariance → overlays + provenance); `configs/scenariogen/{covariance,scenarios}.json`; 22 tests inc. known-co-move recovery. |
| **S2 — Named scenarios v1** | Encode Taiwan / AI-bust / rate-capital / key-man definitions (drivers + overlays + 3 severity tiers); capital (ES-under-shock) output | S1 + VaR/ES | **✅ done** — `catalog.py` (`ScenarioCatalog`: named scenarios × mild/adverse/severe tiers from config); `stress.py` (`ScenarioStressEngine`: full-reval P&L via P2 `ScenarioEngine` + ES-under-shock capital via `FullRevalVarEngine` new `base_transforms=`); `report.py` + `runner.py` CLI. Pricer enablers: `SupportsTransforms` Protocol on `ScenarioEngine.run`; `base_transforms` pre-shock on `historical_var`. 8 tests. Plausibility/GATE checks still S3. |
| **S3 — Governance** | Scenario versioning + provenance + plausibility/GATE checks; scenario store (mirrors `PortfolioStore`) | P4 governance, portfolio versioning | **✅ done** — `versioning.py` (`compute_scenario_version` content-hashes economic content; `ScenarioStore` register/load/versions mirrors `PortfolioStore`); `plausibility.py` (`PlausibilityChecker`: hard per-factor-class **bounds** = gate, n-σ historical **envelope** = graduated flag/fail per tier); runner GATE holds non-promotable scenarios out + registers versions; `scenario_version` stamped into every materialized vector + report. **Found+fixed latent bug:** linear propagation implied sub--100% moves for high-beta names (e.g. MU) → negative prices in S2; added a limited-liability `RETURN_FLOOR` clamp (§ below). `configs/scenariogen/plausibility.json`; 11 tests. |
| **S4 — MacroRAG wiring** | Ingest the driver vector + citations from MacroRAG; quarterly-definition / weekly-vector cadence; calendar-aware refresh | MacroRAG catalog §9B/§11 | **✅ done** — `macro/` subpackage: `client.py` (transport Protocol + `requests` HTTP + `MacroRAGClient` over MacroRAG's live `/query`,`/citations`,`/healthz`; `synthesize()` requests the §9B shock-vector handoff), `models.py` (API + handoff dataclasses), `mapping.py` (shock-vector fields → canonical factor keys + unit conversion), `gate.py` (freshness + citation-coverage GATE, catalog §10), `provider.py` (`MacroScenarioProvider`: macro legs override static drivers, equity stays house/covariance-derived, graceful fallback). `configs/scenariogen/macro.json` (connection `enabled:false` until deployed + per-scenario synthesis requests). Runner `--macro`. 20 tests (fake transport, no network). **MacroRAG synthesis endpoint is now deployed** (per `docs/design/MacroRAG_synthesis_endpoint_spec.md`); `macro.json connection.enabled=true`, `world_view_id` provenance captured. Set `base_url` to the deployed service URL + the API key env var; on any failure the provider still falls back to static drivers. Cadence (quarterly-def/weekly-vector) + calendar awareness are the remaining sub-items. |
| **S5 — Reverse stress + reporting** | Reverse-stress solver (5D); scenario dashboards (P&L + capital by scenario/severity); analyst sign-off workflow | S2 + P3 metrics | **✅ done** — `reverse.py` (`ReverseStressSolver`: bisection on severity λ along a scenario axis to a loss limit → "what breaks us"); `dashboard.py` (`StressDashboard` scenario×severity P&L/capital matrix + ranking + limit breaches + markdown; `ScenarioResultStore` Parquet+DuckDB queryable layer); `signoff.py` (`SignoffStore` append-only approve/reject, latest-wins, `is_approved`) + `signoff_runner.py` CLI. Runner `--reverse-stress`/`--target-loss`, `--require-signoff`, auto dashboard+breach logging + result-store persistence. `configs/scenariogen/limits.json` (loss/capital limits + solver settings). 15 tests. |

---

## 11. Open questions / inputs to confirm with the business

1. **Capital definition:** is "required capital" economic capital (ES at what confidence/horizon),
   a regulatory measure (FRTB-SA/IMA), or an internal limit? Drives the §8(4) output.
2. **Severity calibration policy:** percentile-based (95/99/99.9) vs DFAST-template vs fixed
   house magnitudes — which is the sign-off standard?
3. **Covariance window policy:** full-sample vs EWMA vs fixed crisis windows — and which crises.
4. **Key-man spillover assumptions:** peer-spillover fraction and which peers, per name.
5. **Data sourcing priority:** per-name option-underlier spot history, a cleaner CDX/issuer spread
   series, and a fuller IR curve — to strengthen the covariance (§4B gaps).
6. **Scenario approval/governance owner** and refresh cadence ownership (quant vs risk vs macro).
7. **Propagation guardrails / covariance shrinkage** *(surfaced in S3)*: raw conditional-
   covariance propagation gives some high-beta single names (notably **MU**, est. β≈2.5 to the
   semis complex) co-moves that hit the limited-liability floor (−95%) even at the *adverse*
   tier. S3 added a `RETURN_FLOOR` clamp (correctness — prevents sub−100% moves / negative
   prices) and the envelope flags them as beyond-historical, but the underlying beta estimate is
   inflated by the short aligned-history window + a single AI-correlated regime. Follow-up:
   covariance **shrinkage** (Ledoit-Wolf) and/or longer per-name spot history (§4B gap), and a
   policy decision on whether to cap per-name betas in propagation.

---

*This plan keeps scenarios coherent by construction (covariance-derived co-moves around
narrative-set, MacroRAG-cited drivers, plus explicit idiosyncratic overlays), reuses the existing
bump/score/full-reval engine and provenance discipline, and treats scenario definitions as
governed, versioned, reproducible artifacts — the same standard the rest of the AITrade pipeline
already meets.*
