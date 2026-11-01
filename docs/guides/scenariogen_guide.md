# 🌩️ ScenarioGen — User & Operator Guide

> Generates **correlated stress scenarios**: from a few narrative driver moves it propagates a
> full, covariance‑consistent shock vector across all risk factors, fully reprices the book,
> computes **ES‑under‑shock capital**, runs a **plausibility gate**, versions and **signs off**
> scenarios, and solves **reverse stress**. Driver magnitudes can be sourced three ways:
> **static config**, the **deterministic dual‑RAG** path (`--dual-rag`: MacroRAG macro legs +
> ARag SEC‑filing company legs, fail‑loud), or the **agentic LLM** path (a separate flow — see
> [scenariogen_llm_flow.md](scenariogen_llm_flow.md)).

Source: [src/scenariogen/](../../src/scenariogen/) · Config: [configs/scenariogen/](../../configs/scenariogen/) · Design: [AITrade_stresstest_scenario_def_design.md](../design/AITrade_stresstest_scenario_def_design.md), [scenariogen_named_scenario_dualrag_design.md](../design/scenariogen_named_scenario_dualrag_design.md) · LLM path: [scenariogen_llm_flow.md](scenariogen_llm_flow.md)

---

## 👔 For business users

### What it produces

For each **scenario × severity tier** (mild / adverse / severe):

- A **materialized shock vector** — the narrative drivers (e.g. "TWD −8%, TSM −25%") propagated
  through the **covariance matrix Σ** into a consistent move on *every* factor, plus any
  idiosyncratic **overlays** (e.g. a key‑man name shock).
- **Full‑revaluation P&L** by trade and asset class.
- **ES‑under‑shock capital**: Expected Shortfall computed *under the shocked state* minus today's
  ES — i.e. how much capital the scenario would demand.
- A **plausibility verdict** (pass / flag / fail) and **limit‑breach** flags.
- **Provenance**: scenario version, covariance id, snapshot id, and whether macro drivers were
  used (with citations) or static fallback.

### The named scenarios (default set)

`ai_deflation`, `ai_efficiency_shock`, `taiwan_geopolitical`, `tariff_shock`, `crowded_unwind`,
`inflation_repricing`, `rate_capital`, `ai_rotation`, `keyman_nvda` (see
[configs/scenariogen/scenarios.json](../../configs/scenariogen/scenarios.json) and the
[design](../design/scenario_set_and_ablation_design.md) §4.2). Each has a narrative, a trigger and a
severity block that anchors its tiers to history.

- **Historical replays** (`ai_deflation`, `ai_efficiency_shock`, `tariff_shock`, `crowded_unwind`,
  `inflation_repricing`, `ai_rotation`) carry a `replay` window instead of authored drivers. When the
  catalog loads, their drivers become the episode's observed factor moves, and `horizon_days` becomes
  the `eq:XLK` trading-day count over the window. A one-off company move labelled `idiosyncratic`
  (INTC in `crowded_unwind`) is left to Σ and reported separately. A window the on-disk history
  doesn't cover stops that scenario (fail-loud). The report's `materialized.provenance.replay` block
  records the window and the excluded moves. It also lists factors whose history starts inside the
  window (`partial`). A multi-month replay is one instantaneous shock to today's book: it ignores
  option expiries, carry and rebalancing over the window.
- **Sensitivity library** (`momentum_crash`, `semis_dispersion`, `funding_basis_squeeze`): the
  `sensitivities` block of `scenarios.json` runs only with `--sensitivities`, or when one is named by
  `--scenario`.

### Governance built in
- **Plausibility gate** holds out scenarios whose moves breach hard sanity bounds; flags (but
  still runs) scenarios that merely exceed historical norms — which is *expected* for stress.
- **Sign‑off**: scenarios are content‑versioned; an analyst approves a version, and production
  runs can be told to admit only approved versions.
- **Reverse stress**: instead of "what does this scenario lose?", asks "**what severity would it
  take to lose our limit?**".

---

## 🛠️ For technical operators

### Prerequisite

A priced portfolio + market‑data snapshot (it reuses the pricer's engine and the risk full‑reval
VaR engine) and aligned factor history. Run market data and the pricer first.

### Stress runner

```powershell
$env:PYTHONPATH = $PWD.Path   # the repo root
python -m src.scenariogen.runner [flags]
```

Source: [src/scenariogen/runner.py](../../src/scenariogen/runner.py).

| Flag | Default | Meaning |
|---|---|---|
| `--asset-class` | all | Restrict to one asset class. |
| `--valuation-date` | portfolio default | ISO date. |
| `--book` | active default (`core`) | Book variant to stress: `base`/`balanced`/`core`/`aggressive`/`risky` (parity with the pricer/risk runners). |
| `--scenario` | all | One scenario id, e.g. `taiwan_geopolitical`. |
| `--severity` | all | One tier: `mild` / `adverse` / `severe`. |
| `--no-capital` | off | Skip ES‑under‑shock capital (much faster). |
| `--var-lookback N` | config | Historical‑VaR lookback days. |
| `--cov-lookback N` | config | Covariance window lookback days. |
| `--no-gate` | off | Run even non‑promotable (plausibility‑FAIL) scenarios. |
| `--macro` | off | Source macro‑leg drivers from **MacroRAG** only (graceful static fallback). |
| `--dual-rag` | off | **Deterministic dual‑RAG**: MacroRAG macro legs **+ ARag** SEC‑filing company legs, with a fail‑loud service preflight. Supersedes `--macro`. |
| `--no-narrative` | off | Skip the gpt‑5 value‑add attribution narrative (dual‑RAG runs). |
| `--no-exit-value` | off | Skip the P4 liquidity‑horizon exit value (F1) + cash/collateral line (F2) post‑processing. |
| `--require-signoff` | off | Only run analyst‑approved scenario versions. |
| `--reverse-stress` | off | Solve the severity that drives loss to the target. |
| `--target-loss USD` | `limits.json loss_limit` | Reverse‑stress target loss. |

```powershell
python -m src.scenariogen.runner                                        # all scenarios × tiers
python -m src.scenariogen.runner --scenario taiwan_geopolitical --severity severe
python -m src.scenariogen.runner --no-capital                           # fast
python -m src.scenariogen.runner --dual-rag                             # MacroRAG + ARag (fail-loud)
python -m src.scenariogen.runner --dual-rag --require-signoff           # production-style
python -m src.scenariogen.runner --reverse-stress --scenario rate_capital --target-loss 50000000
```

### Sign‑off CLI

```powershell
python -m src.scenariogen.signoff_runner --list
python -m src.scenariogen.signoff_runner --approve taiwan_geopolitical --note "Q3 review"
python -m src.scenariogen.signoff_runner --reject ai_deflation --version <hash> --approver risk_team
```

Mutually exclusive `--approve` / `--reject` / `--list`; optional `--version`, `--approver`
(default OS user), `--note`. Source: [src/scenariogen/signoff_runner.py](../../src/scenariogen/signoff_runner.py).
Sign‑off is append‑only; the latest decision per version wins.

### Set coverage report

```powershell
python -m src.scenariogen.coverage_runner                       # latest stress report, mild tier
python -m src.scenariogen.coverage_runner --report data/pricer/scenario/<date>/scenario_<run_id>.json --workers 4
```

Answers "is there a way this book loses money that no scenario represents?" (design §7.3). It reprices
each trade over the full-revaluation VaR history. From that history it takes the book's principal loss
directions, and reports how much of each the scenarios' per-trade P&L spans; a direction spanned less
than half is flagged `uncovered`. It also reports diversity: the loss-weighted participation ratio of
the scenarios once the book's first direction is removed, with a target of 3–4. Pairs above 0.85 cosine
on those vectors are listed as merge candidates. Scenarios with no result at the tier (held by the gate)
are listed and left out. Parameters: [coverage.json](../../configs/scenariogen/coverage.json).
Source: [src/scenariogen/coverage_runner.py](../../src/scenariogen/coverage_runner.py).

### Decision impact (scenario losses as a sizing constraint)

```powershell
python -m src.scenariogen.decision_impact_runner                                      # latest stress report
python -m src.scenariogen.decision_impact_runner --ablation-report data/pricer/scenario/<date>/ablation_<run_id>.json
```

Shows whether the scenarios change a sizing decision (design §7.6). Each profile is sized twice: as
today (VaR budget and caps), and with a stress-loss constraint on top. That constraint caps the worst
**adverse** scenario loss at 6 × the profile's daily VaR budget (`stress_constraint` in
[sizing.json](../../configs/pricer/sizing.json)). The scenario vectors are revalued on the unsized base
book. The report gives, per profile, the budget, the worst scenario before and after, the scenarios
over budget, the binding constraint, and the change in each trade's size, VaR, ES, expected return and
gross leverage. With an ablation report it does this per layer subset, and adds a "without each source"
table: what removing one enrichment source changes. Analysis only: no book variant or `portfolio*.json`
is written. Source:
[src/scenariogen/decision_impact_runner.py](../../src/scenariogen/decision_impact_runner.py).

### Outputs

| Output | Path |
|---|---|
| Stress report (P&L, capital, plausibility per scenario×tier; + P4 `liquidity`/`collateral` exit-value & cash line, F8/F9 `geopolitical`/`throughput` calibration, and — on `--dual-rag` — `news`/`crowding` per-name multipliers) | `data/pricer/scenario/<date>/scenario_<run_id>.json` |
| Set coverage / diversity report | `data/pricer/scenario/<date>/coverage_<stress run_id>.json` |
| Scenario result store (Parquet, DuckDB‑queryable) | `data/pricer/store/scenario_results/` |
| Immutable scenario version specs | `data/pricer/store/scenario/` |
| Sign‑off records (append‑only) | `data/pricer/store/signoff/` |
| Scenario run manifest | `data/scenariogen/store/runs/` |

### Retention / GC

```powershell
python -m scripts.scenariogen.gc      # prune per-run stores (see scripts/scenariogen/gc.py)
```

Uses the shared retention engine ([src/common/store/retention.py](../../src/common/store/retention.py));
prunes per‑run stores (results/manifests) — **never** the content‑addressed version store.

---

## ⚙️ Configuration

| File | Purpose |
|---|---|
| [covariance.json](../../configs/scenariogen/covariance.json) | How Σ is estimated: `lookback_days`, `window` (`full`/`ewma`/`crisis`), `ewma_lambda` (0.94), named `crisis_windows`, `include_style` (adds the F3/F4/F6 driver-only factors), and the F7 `implied_corr_regime` block (COR3M percentile → market-implied shrinkage). |
| [liquidity.json](../../configs/scenariogen/liquidity.json) · [collateral.json](../../configs/scenariogen/collateral.json) | **P4 exit value & funding** (mode-iii, `--no-exit-value` to skip): the signed-off Basel/FRTB liquidity-horizon table + bid-ask half-spreads (F1), and the margined-trade tag + IM proxy + `SOFR−IORB` funding gauge (F2). |
| [news.json](../../configs/scenariogen/news.json) · [crowding.json](../../configs/scenariogen/crowding.json) | **P3/F5 per-name calibration** (mode-ii, dual-RAG only): the clamped multipliers on the ARag idiosyncratic leg from 8-K/GDELT news and FINRA days-to-cover crowding (composed: `ARag × news × crowding`); `keyman_items` + `short_names`/`squeeze_percentile`. |
| [geopolitical.json](../../configs/scenariogen/geopolitical.json) · [throughput.json](../../configs/scenariogen/throughput.json) | **F8/F9 scenario severity multipliers** (mode-ii): GPR percentile → `taiwan_geopolitical` amplifier + live trigger; semiconductor-IP YoY percentile → `ai_deflation` multiplier (inverse — decelerating throughput amplifies). Applied to scenarios tagged `geopolitical`/`throughput` in `scenarios.json`. |
| [scenarios.json](../../configs/scenariogen/scenarios.json) | Scenario definitions: `severity_tiers` (mild 1.0 / adverse 1.5 / severe 2.0), per‑scenario `drivers`, `overlays`, `method`, `horizon_days`. |
| [plausibility.json](../../configs/scenariogen/plausibility.json) | Hard `bounds` per factor class (breach = **FAIL**) and an `envelope` (n‑σ historical band, breach = **FLAG**). |
| [limits.json](../../configs/scenariogen/limits.json) | `loss_limit`, `capital_limit`, and reverse‑stress solver settings (`max_scale`, `tol_rel`, `max_iter`). |
| [macro.json](../../configs/scenariogen/macro.json) | MacroRAG `connection` (base_url, `api_key_env`, retry/breaker), `gate` (freshness/citations), and per‑scenario synthesis questions. |
| [arag.json](../../configs/scenariogen/arag.json) | ARag `connection` (base_url — deliberately `:8001`, must not collide with MacroRAG `:8000` — plus retry/breaker and the run‑level `budget` call/wall‑clock ceilings), `gate` (`freshness_years`), and per‑scenario `required_services` + `companies[]` (ticker, seed_move, narrative_query, calibration). Used by `--dual-rag`. |
| [llm/](../../configs/scenariogen/llm/) (`llm.json`, `mcp.json`, `pricing.json`) | The agentic LLM path: model tiers, budgets, the two MCP server launch specs, and the gpt‑5 rate card. See [scenariogen_llm_flow.md](scenariogen_llm_flow.md). |

Driver/overlay unit keys: `move_bp` (÷10000), `move_pct` (÷100), `move_pts` (÷100),
`move_rel` (as‑is), `move` (raw). Canonical factor keys (`ir:DGS10`, `eq:NVDA`, `fx:TWD`,
`eqvol`, `ratesvol`, `credit`, …) are defined in [src/scenariogen/factor_keys.py](../../src/scenariogen/factor_keys.py).

---

## 🤖 RAG dependencies (`--macro`, `--dual-rag`)

Both MacroRAG and ARag are **separate REST services in sibling repos** (`../MacroRag`,
`../XbrlRag`) with their own LLMs + vector databases; this repo only calls their APIs (no
vector DB lives in Faultline). Their location is env‑overridable in one place — set
`MACRORAG_BASE_URL` / `ARAG_BASE_URL` (see the `${VAR:-default}` placeholders in `macro.json` /
`arag.json`).

**`--dual-rag` (deterministic, preferred for named scenarios).** MacroRAG sets the scenario's
*macro drivers*; **ARag** sets each covered name's *idiosyncratic company overlay* grounded in
what that company discloses in its SEC 10‑K/Q Risk Factors (`final(eq:NVDA) = systematic(macro) +
idiosyncratic(ARag)`). Unlike `--macro`, dual‑RAG is **fail‑loud**: a `preflight_services` probe
aborts the whole run up front if a *required* service is down (`ServiceUnavailableError`), a
per‑scenario ARag failure raises `ScenarioGenerationError` (that scenario is skipped), so
does a scenario whose `arag.json` `required_services` lists `macro` when its macro legs were not
applied (reason `macro_synthesis_failed` | `macro_unavailable` | `macro_gate_failed` |
`macro_not_configured`), and a breached run‑level ARag budget hard‑stops the run — it never silently substitutes static shocks.
Every MacroRAG/ARag call is captured to a trace JSONL, and value‑add attribution + an optional
gpt‑5 narrative are produced (`--no-narrative` to skip). Design:
[scenariogen_named_scenario_dualrag_design.md](../design/scenariogen_named_scenario_dualrag_design.md),
[harness_audit_202607.md](../design/harness_audit_202607.md).

**`--macro` (MacroRAG only, graceful).** Sources just the *macro leg* from MacroRAG with a
**graceful static fallback**: if MacroRAG is disabled/unreachable/gate‑failed it logs
`scenariogen_macro_fallback_total{reason}` and completes on static config drivers.

- **Client**: [src/scenariogen/macro/client.py](../../src/scenariogen/macro/client.py) — talks to
  `/healthz`, `/query` (mode=synthesize), `/citations/{id}`.
- **Connection** (from [macro.json](../../configs/scenariogen/macro.json)): `base_url`
  (default `http://localhost:8000`), API key from the env var named in `api_key_env`
  (**`MACRORAG_API_KEY`**), `timeout_s`, retry with exponential backoff, and a **circuit breaker**
  (opens after `breaker_threshold` consecutive failures).
- **Request**: the synthesize call sends `synthesis.horizon_days` (the scenario's `horizon_days`
  in `scenarios.json`), `synthesis.fields` (its `macro.json` `expect_fields`) and
  `synthesis.directions` (its `macro.json` `directions`: field → `up`|`down` under MacroRAG's sign
  convention: UST up = yields rise, OAS up = widen, MOVE/VIX up = vol rises, USDTWD/USDJPY up =
  TWD/JPY depreciate, REAL_YIELD_10Y/TERM_PREMIUM_10Y up = the 10Y TIPS real yield / ACM term
  premium rises). MacroRAG pins moves to the horizon, emits only the listed fields and flags
  a wrong-signed field `direction_mismatch`. A `directions` value other than `up`/`down`, or a key
  missing from `expect_fields`, raises `ConfigError` when the provider is built.
- **Field drop**: a field MacroRAG flags `implausible_magnitude`, `unit_scale_mismatch`,
  `horizon_mismatch`, `direction_mismatch` or `card_value_mismatch` (MacroRAG's full hard-code set)
  is dropped as if absent, whatever MacroRAG's own `drop_invalid` setting. Each applied leg's
  provenance carries `grounding_mode`, `source_horizon_days` and `validation`.
- **Field mapping**: UST tenors → `ir:DGS2/5/10/30` (parallel `UST` → every node), `IG_OAS` (else
  `HY_OAS`) → `credit`, `MOVE` → `ratesvol`, `VIX` → `eqvol`, `USDTWD`/`USDJPY` → `fx:TWD`/`fx:JPY`,
  `REAL_YIELD_10Y`/`TERM_PREMIUM_10Y` → the driver-only `real:DFII10`/`tp:ACMTP10` (bp × 1e-4). The
  real-yield and term-premium drivers are conditioned on jointly with any nominal `ir:` leg, not
  added to it. Any other field (`EQUITY_US`, `MOVE_*`, `XCCY_BASIS`) has no factor and is listed in
  the macro resolution's `unmapped_fields`.
- **Curve coherence**: when MacroRAG supplies any UST tenor, the authored `ir:*` drivers it did not
  supply are removed (the engine propagates them from MacroRAG's tenors) and listed in the macro
  resolution's `superseded_static`, so authored and MacroRAG tenors are never spliced into one curve.
- **Gate**: every returned driver must cite ≥1 chunk, each present and dated, and no cited chunk
  older than `freshness_days` (deployed `macro.json` value **550**; the code default of 120 is
  overridden by config), except `market_episode` cards (realized moves measured from FRED over a
  stated horizon), which skip only the age check. A failure falls the scenario back to static
  (or, under `--dual-rag` when macro is required, stops it).
- **Graceful fallback**: if MacroRAG is disabled, the key is unset, `healthz` fails, synthesis
  fails, or the gate fails, the run **falls back to static config drivers** and logs
  `scenariogen_macro_fallback_total{reason}` — the run always completes.

```powershell
$env:MACRORAG_API_KEY = "your-key"
python -m src.scenariogen.runner --macro
```

> `OPENAI_API_KEY` in `.env` is consumed by the MacroRAG **service**, not by ScenarioGen. From
> Faultline's side the only secret you set is `MACRORAG_API_KEY` (and only when using
> `--macro`). Contract details: [MacroRAG_synthesis_endpoint_spec.md](../design/MacroRAG_synthesis_endpoint_spec.md).

---

## 🔗 How other subsystems consume scenario output

- **The UI/BFF** exposes `/api/scenarios/*`: catalog (scenario×tier grid), detail with
  shock‑derivation (correlation heatmap + origin‑tagged vector), results/capital, versions,
  sign‑off, and MacroRAG health — see [ui_guide.md](ui_guide.md). The scenario report must
  exist for those pages to populate.
- **The pricer/risk engine** is the execution layer: a `MaterializedScenario` is converted to
  bump transforms and run through the same `ScenarioEngine` full revaluation used by risk —
  scenarios are market‑data perturbations + full repricing, no pricing‑code changes.
- **Programmatic / DuckDB:**

```sql
SELECT scenario, severity, total_pnl, capital_delta, used_macro
FROM read_parquet('data/pricer/store/scenario_results/**/*.parquet')
WHERE valuation_date = '2026-10-06'
ORDER BY capital_delta DESC;
```

---

## ⚠️ Known limitations

- **A replay's scale cap can be set by a name with partial history** (found 2026-10-19, deferred).
  A historical replay (e.g. `ai_deflation`, 2021-11-29 → 2022-10-14) asserts each factor's observed
  move over the window, and its anchored tiers are capped at the largest scale that keeps every bounded
  factor inside its hard plausibility bound (`anchoring.anchor_tier`, `severity.affine_scale_cap`). A
  name that started trading inside the window has its "observed move" measured from its first price,
  not from the window start. CEG (spun off from Exelon, first traded 2022-02) shows +93.5% over the
  `ai_deflation` window, so `eq:CEG` binds the cap at every tier: on the 2026-10-19 dual-RAG check the
  mild and adverse tiers come out identical (−$95.1M) and severe barely larger (−$96.5M). The
  BFF's replay-window card already flags such names as partial history. Possible fix, not scheduled:
  leave names whose history starts after the window start out of the cap (their move still counts in
  the scenario), or out of the replay drivers.

---

## 🚑 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Scenario "held out" (gate fail) | A move breaches a hard `bounds` cap | Check the driver/propagated magnitude vs [plausibility.json](../../configs/scenariogen/plausibility.json); fix the driver or, for diagnostics, `--no-gate`. |
| Everything flagged "beyond‑historical" | Envelope (n‑σ) breach — expected for severe tiers | Informational flag; default still promotable. Tune `envelope.n_sigma` if needed. |
| `--require-signoff` runs nothing | No approved versions, or the spec changed (new hash) | `signoff_runner --list`; re‑approve the current version. |
| MacroRAG falls back to static | Disabled / key unset / service down / gate fail | Set `MACRORAG_API_KEY`, start MacroRAG at `base_url`; check the logged `reason`. |
| MacroRAG circuit breaker open | Too many consecutive failures | Service is unhealthy — fix it; breaker closes on the next success. |
| Run very slow | ES‑under‑shock capital reprices per scenario | Use `--no-capital` for a quick pass, or restrict `--scenario`/`--severity`. |
| Σ looks degenerate / NaNs | Too‑short covariance window or gappy factor history | Increase `--cov-lookback`; verify the factor CSVs; try `window: ewma`. |
| Reverse stress "not reachable" | Target loss exceeds `max_scale × drivers` | Raise `reverse_stress.max_scale` or lower `--target-loss`. |

**Common to stress‑testing systems:** correlated propagation can produce surprisingly large moves
on factors you didn't name — that's the point (Σ links them), but verify against the plausibility
bounds. A scenario that "loses nothing" usually means the book has no exposure to its drivers, not
that the scenario is broken.
