# QuantLib Pricing & Risk Pipeline — Architecture Review and Target-State Design

Author: Architecture review (Claude)
Date: 2026-06-20
Scope: Technical architecture of the `src/pricer` pricing pipeline — serving model, scalability,
observability, reproducibility, risk coverage, and operational readiness. This document is a
forward-looking design; it does **not** change the portfolio scope (that lives in
`AITrade_quantlib_portfolio_marketdata.md`).

> **How to read this.** §1 assesses the current system honestly. §2 defines the essential
> capabilities a modern pricing & risk platform needs. §3 is the prioritized backlog
> (P0 highest → P5 lowest), each item with *Problem → Change → Benefit → Effort*. §4 sketches the
> target architecture and a phased roadmap. Recommendations are grounded in the current code;
> file references are clickable.

---

## 1. Current State — What Exists Today

### 1.1 What the system does well

The pricing module ([src/pricer/](../../src/pricer/)) is genuinely well-factored for its age. The
core design is sound and worth preserving:

- **Clean 3-layer separation.** `input/` (pure domain objects, no QuantLib import — mockable),
  `loader/` (CSV → domain adapters with staleness logic), `ql/` (the only layer importing
  QuantLib). This is the right shape and most of the recommendations below build *on top of* it
  rather than replacing it.
- **Declarative market-data requirements.** Each trade declares what it needs via
  `market_data_requirements(ctx)`; the loader resolves generically. This is an extensible,
  data-driven dispatch pattern.
- **Multi-curve-ready interface.** Trades request DISCOUNT and FORECAST curves separately even
  though SOFR-OIS resolves both to one object today ([base_pricer.py:41](../../src/pricer/ql/common/base_pricer.py#L41)).
  A basis/XCCY curve drops in with no caller change.
- **Per-trade fault isolation.** One bad trade does not kill the run
  ([runner.py:96](../../src/pricer/runner.py#L96)).
- **A real test suite.** Mocked + real-data tests under [tests/pricer/](../../tests/pricer/).
- **All 38 trades price** across 6 asset classes (rates, bonds, options, cash equity, credit, FX).

### 1.2 Architecture as-built

```
                          ┌─────────────────────────────────────────┐
   data/marketdata/*.csv  │  src/marketdata/ (separate downloaders)  │
   (flat files, gitignored)└────────────────────┬────────────────────┘
                                                 │ (offline, manual cadence)
                                                 ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │  python -m src.pricer.runner   (single process, single thread)            │
  │                                                                            │
  │   load_portfolio()        portfolio/portfolio.json                         │
  │        │                                                                   │
  │   for trade in trades:    ◄── SEQUENTIAL loop, no parallelism              │
  │     requirements ─► MarketDataLoader.resolve() ─► curves REBUILT per trade │
  │                          (cache is per-resolve, not shared across book)    │
  │     get_pricer(trade).price()  ◄── sets GLOBAL ql evaluationDate           │
  │     write_result() ─► data/pricer/PV/<asset>/<trade>/<date>.json (local FS)│
  └──────────────────────────────────────────────────────────────────────────┘
                                                 │
                          data/pricer/PV/.../*.json  +  summary_<date>.csv
                          (local files; no store, no API, no query layer)
```

### 1.3 Gaps (the honest assessment)

| Area | Current state | Gap |
|---|---|---|
| **Serving** | Batch CLI only (`runner.py`). | No service/API; consumers can only read JSON off disk. README promises "K8S" but [infra/pricer/](../../infra/pricer/) is **empty** — no Dockerfile, no manifests. |
| **Scalability** | Sequential `for trade in trades` loop ([runner.py:78](../../src/pricer/runner.py#L78)); the loader cache is local to each `resolve()` call ([market_data_loader.py:41](../../src/pricer/loader/market_data_loader.py#L41)) so **curves are rebuilt per trade**; CSVs re-read from disk per trade. | No parallelism; no shared curve cache across the book; no horizontal scale path. |
| **Concurrency safety** | `ql.Settings.instance().evaluationDate` is set globally on every `price()` ([base_pricer.py:29](../../src/pricer/ql/common/base_pricer.py#L29)). | Process-global mutable state — **cannot price two valuation dates concurrently in one process.** This constrains how parallelism must be implemented. |
| **Reproducibility / audit** | `PricingResult.to_dict()` ([pricing_result.py:32](../../src/pricer/ql/common/pricing_result.py#L32)) emits PV + measures + warnings. | No code version (git SHA), no market-data snapshot ID, no config hash, no QuantLib version, no run ID, no wall-clock timestamp. **A price cannot be reproduced or audited later.** |
| **Risk** | PV only. A few measures (dv01, vega) appear ad hoc. Stress data (MOVE, FRED macro, power) is *downloaded* but unused. | No systematic sensitivities, no scenario/stress engine, no VaR/ES, no P&L explain — despite "Risk" being in scope. **Largest functional gap.** |
| **Observability** | Python `logging` to stdout + `logs/pricer.log` ([runner.py:36](../../src/pricer/runner.py#L36)). | No structured logs, no metrics, no tracing, no dashboards, no alerting. Failures land silently in a CSV `status` column. |
| **Data quality** | Staleness *warns* but never blocks ([staleness.py:35](../../src/pricer/loader/staleness.py#L35)). CSV columns read positionally/by name with no schema check. | No data-quality **gate**, no schema/data contract validation, no immutable point-in-time snapshot store. |
| **Config & secrets** | Raw `json.load` ([config.py:18](../../src/pricer/config.py#L18)); no validation. API keys via dotenv. | No schema validation, no env separation (dev/uat/prod), no secrets manager. |
| **Build / CI-CD** | `requirements.txt` uses loose `>=` pins; manual conda env. | No pinned/reproducible build, no container, no CI (lint/type/test), no CD. **QuantLib version drift silently changes prices.** |
| **Results storage** | Per-trade JSON files + a run summary CSV. | No queryable store, no history/time-series, no aggregation, non-atomic writes. |

---

## 2. Capabilities a Modern Pricing & Risk Platform Needs

Framing the target. A production valuation & risk system is judged on six axes:

1. **Correctness & model governance** — prices are right, validated, and independently verifiable;
   models are documented and version-controlled (SR 11-7 / model-risk discipline).
2. **Reproducibility & auditability** — any past number can be reproduced bit-for-bit from pinned
   inputs (market-data snapshot + code + config + library versions). Non-negotiable for finance.
3. **Risk, not just price** — sensitivities, scenarios/stress, VaR/ES, and P&L attribution.
4. **Serving & scale** — EOD batch *and* on-demand pricing; scales with book size and scenario
   count; predictable latency.
5. **Observability & operability** — you can see what ran, how long it took, what failed, and
   whether the numbers moved more than expected — and get alerted.
6. **Data management** — governed, point-in-time, quality-gated market data with contracts.

The current system is strong on (1)'s *engine* foundations and weak on (2)–(6). The backlog below
is ordered to fix the foundational enablers first.

---

## 3. Prioritized Recommendations

Priorities reflect *risk-adjusted value*: cheap changes that unlock auditability and safe scaling
come before expensive build-outs. The user named **serving, scalability, observability** — those
are P1/P3 here, but two foundational items (reproducibility, data gating) are deliberately ranked
above them because scaling or serving an unauditable, ungated pipeline multiplies the wrong thing.

### P0 — Foundational: make every price reproducible and auditable

**Problem.** `PricingResult` carries no provenance. Given a JSON in `data/pricer/PV/...`, you
cannot answer "what curve, what code, what library produced this?" In a financial system this is
disqualifying — it breaks audit, regulatory defense, and any "why did the number change?"
investigation.

**Change.**
- Extend `PricingResult` with a `provenance` block stamped at write time: `run_id` (UUID),
  `git_sha`, `quantlib_version`, `python_version`, `config_hash`, `market_data_snapshot_id`
  (see P0b), `valuation_date`, `priced_at` (UTC timestamp), and `pricer_class`.
- Capture `git_sha` and `quantlib_version` once at process start; thread the snapshot ID from the
  loader into the result.
- Write results **atomically** (temp file + `os.replace`) so a crashed run never leaves a partial
  JSON that looks valid.

**Benefit.** Full lineage on every number; reproducible re-pricing; audit-ready; enables the
"PV moved — why?" workflow that everything else depends on. **Effort: S** (small, localized to
`pricing_result.py`, `result_writer.py`, `runner.py`).

### P0b — Immutable, point-in-time market-data snapshots with a snapshot ID

**Problem.** Market data is loose CSVs (gitignored), re-read per trade, selected by an in-file
`as_of_date` column. There is no single immutable "this is the market as of T" object and no ID to
pin. Two runs days apart over the same valuation date can silently use different underlying files.

**Change.**
- Introduce a **MarketDataSnapshot** abstraction: an immutable, content-addressed set of the exact
  inputs used for a run, identified by a hash (`market_data_snapshot_id`). Persist a manifest
  (which files, which `as_of` rows, checksums).
- Load the snapshot once per run; pass it to every trade. (Today's per-`resolve` re-reads become a
  one-time load — this also fixes the per-trade I/O noted under scalability.)
- Stamp the snapshot ID into every result (P0).

**Benefit.** Point-in-time correctness, reproducibility, and the substrate for scenario/stress
(P2 bumps a *copy* of the snapshot). **Effort: M.**

### P0c — Turn data staleness into a governed quality gate

**Problem.** [staleness.py](../../src/pricer/loader/staleness.py) only appends a *warning*; a curve
19 days stale still prices, and the warning can be lost in a long log. There is no schema
validation on the CSVs — a renamed/missing column fails deep inside pricing with an opaque error.

**Change.**
- Add a **policy layer**: each market-data kind gets a max-staleness and required-fields policy.
  Breach → configurable action (`warn` | `block` | `substitute`). Default EOD policy should
  **block** on hard inputs (the binding SOFR curve) and warn on soft ones.
- Validate CSV schemas at ingestion with a data-contract library (`pandera`/`pydantic`): column
  names, dtypes, ranges (e.g. vol ∈ [min_iv, max_iv], spreads ∈ [min_bp, max_bp] — values already
  present in [pricer_config.json](../../configs/pricer/pricer_config.json)).
- Surface a per-run **data-quality report** alongside the summary.

**Benefit.** No silent pricing on bad/stale data; fast, clear failures; the quality gate becomes an
observable, alertable signal. **Effort: M.**

### P0d — Pin dependencies and containerize; add CI

**Problem.** [requirements.txt](../../requirements.txt) uses `>=` everywhere and `QuantLib>=1.42`.
QuantLib minor versions change numerical results — a `pip install` six months from now can move
prices with no code change. [infra/pricer/](../../infra/pricer/) is empty despite the README's "K8S"
claim. No CI runs the existing tests.

**Change.**
- Pin exact versions (lockfile: `pip-tools`/`conda-lock`). Treat the QuantLib version as part of
  the model definition (it's already in the provenance block, P0).
- Add a **Dockerfile** that builds the py312 env reproducibly; this becomes the unit of deployment
  for batch jobs and (later) the service.
- Add **CI** (GitHub Actions or similar): lint (`ruff`), type-check (`mypy`), run `tests/pricer/`
  on every push, fail on regression.

**Benefit.** Reproducible builds (numerically stable prices), deployable artifact, regression
safety net. Enables every serving/scaling item below. **Effort: M.**

### P1 — Scalability: parallelize the book with a shared, snapshot-scoped curve cache

**Problem.** The runner prices trades sequentially ([runner.py:78](../../src/pricer/runner.py#L78))
and the loader's memoization cache is **local to each `resolve()` call**
([market_data_loader.py:41](../../src/pricer/loader/market_data_loader.py#L41)) — so the SOFR curve is
bootstrapped *once per trade* instead of once per book. With 38 trades this is wasteful; at 300 or
3,000 it is the bottleneck. The hard constraint: `ql.Settings.evaluationDate` is **process-global**
([base_pricer.py:29](../../src/pricer/ql/common/base_pricer.py#L29)), so naive threading across
different valuation dates is unsafe.

**Change.**
- **Shared curve/vol cache keyed by `(snapshot_id, ccy, curve_purpose, index)`**, built once per
  run and reused across all trades. (The per-resolve cache logic already exists — lift it to
  run scope and key it by snapshot.) Pre-build all distinct curves once, up front.
- **Parallelize across trades** using **process-level** workers (one valuation date per process to
  respect the global eval-date), partitioned by asset class. `multiprocessing`/`joblib` now; a
  distributed executor (Ray/Dask, or K8s indexed Jobs sharding the portfolio) later.
- Make the pricing step a **pure function** `price(trade, snapshot, ctx) -> result` with no global
  state beyond the per-process eval date — this is what makes distribution safe and is also the
  natural unit for a service (P1b).

**Benefit.** Order-of-magnitude throughput on large books and (critically) on scenario fan-out,
where the same book is repriced under N market states. Curve-build cost amortized once. **Effort: M.**

### P1b — Separate the pricing *library* from the execution *driver*; define a service interface

**Problem.** Pricing logic and the batch CLI are entangled in `runner.py`. There is exactly one way
to get a price: run the whole batch and read a file. There is no request/response path for
on-demand pricing (pre-trade what-if, intraday re-mark, a UI).

**Change.**
- Factor a stable **`PricingEngine` API**: `price(trade, snapshot, ctx) -> PricingResult` and
  `price_portfolio(trades, snapshot, ctx) -> list[PricingResult]`. The CLI becomes one thin caller.
- Add a **serving facade** (later, behind a flag): a stateless FastAPI/gRPC service exposing
  `POST /price` (single trade, given a snapshot ID) and `POST /price-portfolio`. Stateless workers
  load an immutable snapshot by ID → horizontally scalable, trivially containerized.
- Keep **two execution modes** sharing one engine: **scheduled EOD batch** (K8s CronJob/Airflow,
  the system of record) and **on-demand service** (low-latency what-if).

**Benefit.** Decouples consumers from the filesystem; enables interactive/what-if use; same code
path for batch and service (no model drift between them). **Effort: M–L.**

### P1c — A results store with a query/API layer

**Problem.** Results are per-trade JSON + a summary CSV ([result_writer.py](../../src/pricer/output/result_writer.py)).
You cannot ask "PV history of TRADE_22", "book PV by asset class over time", or "all trades with
warnings last run" without globbing files. `portfolio.json` is similarly a flat file.

**Change.**
- Persist results to a **columnar store** (Parquet partitioned by `valuation_date`/`asset_class`,
  queryable via DuckDB) for analytics, and/or a relational DB for the system of record. Keep the
  JSON as the human-readable per-trade artifact.
- Expose a thin **results API** (or just DuckDB views) for time-series, book aggregation, and the
  PV-explain workflow.
- Move the portfolio (and trade economics — currently *representative placeholders*) into the same
  governed store with versioning.

**Benefit.** Historical analytics, aggregation, P&L explain, regulatory reporting — all impossible
on loose files today. **Effort: M.**

### P2 — Build the Risk engine (the biggest functional gap)

**Problem.** The system computes **PV only**, yet "Risk" is in its name and stress data (MOVE,
FRED macro, EIA power) is already being downloaded and sits unused. A pricing system without
sensitivities and scenarios is half a risk system.

**Change.** Layer risk *on top of* the now-pure pricing function (P1) and snapshot abstraction (P0b):
- **Sensitivities (Greeks).** Bump-and-revalue framework: perturb a copy of the snapshot
  (curve nodes for DV01/key-rate, spreads for CS01, vols for vega, spot for delta/gamma) and
  reprice. The multi-curve, declarative-requirements design makes this natural. (Consider AAD
  later for speed, but bump-and-revalue first — simple, model-agnostic, parallelizes via P1.)
- **Scenario / stress engine.** Apply named shocks (the design doc's Taiwan-geopolitical, AI
  capex boom/bust) to a snapshot and reprice the book. The stress data already downloaded becomes
  the calibration source. Output scenario P&L per trade and book.
- **VaR / Expected Shortfall.** Historical-simulation VaR from the snapshot history once the
  market-data store (P0b) retains history.
- **P&L explain / attribution.** Decompose day-over-day PV change into market moves vs. time vs.
  new trades — using the provenance + snapshot diffs from P0/P0b.

**Benefit.** Turns a pricer into a risk system; unlocks the stated weekly-stress use case; the
single highest-value *functional* addition. **Effort: L** (but incremental — sensitivities first,
then scenarios, then VaR).

### P3 — Observability: structured logging, metrics, tracing, alerting

**Problem.** Operability is `print`-grade: text logs to stdout + a file. Failures land in a CSV
`status` cell ([runner.py:98](../../src/pricer/runner.py#L98)) with no alert. There is no way to chart
pricing latency, failure rate, staleness counts, or PV drift over time.

**Change.**
- **Structured logging.** Emit JSON logs with `run_id`, `trade_id`, `asset_class`, `snapshot_id`,
  duration, status — so logs are queryable, not grepped.
- **Metrics** (Prometheus/OpenMetrics): per-run trade count, success/failure counts, per-asset-class
  and per-trade pricing latency, curve-build time, count of staleness/quality-gate breaches.
- **Tracing** (OpenTelemetry): span per trade (load → resolve → price → write) to find slow trades
  and shared-curve contention.
- **Dashboards & alerts**: run health, data freshness, and **PV-drift alerts** — flag any trade
  whose PV moved more than a tolerance vs. the prior run (catches data/model breakage early).
- **Run manifest**: one record per run (who/what/when/snapshot/pass-fail counts) for the audit trail.

**Benefit.** You can *see* the system, prove EOD completed, and get paged when data goes stale or a
PV jumps — instead of discovering it downstream. **Effort: M.**

### P4 — Model governance & price-quality controls

**Problem.** Several values are pragmatic placeholders: config-fallback issuer Z-spreads, a flat
Bachelier vol, fixed-for-fixed CCS simplification, representative trade economics. These are
*documented* (good) but not *controlled* — there is no regression gate that fails the build if a
refactor moves a PV, and no independent price verification (IPV).

**Change.**
- **Golden/regression PV tests with tolerances.** Freeze known-good PVs for all 38 trades against a
  pinned snapshot; CI fails if any moves beyond tolerance. (Extends the existing real-data tests.)
- **Independent price verification** where an analytic or alternative engine exists (the codebase
  already cross-checks Bermudan FD vs. tree, and par-swap round-trips to 0 — formalize these as
  asserted controls).
- **Model inventory & validation notes**: a short registry of each pricer's model, assumptions,
  known limitations, and approved-use scope (model-risk discipline). Much of this already exists in
  the memory/notes — promote it to a governed artifact.

**Benefit.** Refactors and library upgrades can't silently change prices; assumptions are
defensible and reviewable. **Effort: M.**

### P5 — Config validation, secrets, and environment separation

**Problem.** `load_pricer_config` is a raw `json.load` with no schema ([config.py:18](../../src/pricer/config.py#L18));
a typo'd key fails late and obscurely. API keys live in dotenv. There is no dev/uat/prod split.

**Change.**
- Validate config with **pydantic** models (typed, defaulted, fail-fast with clear messages).
- Externalize per-environment config; load secrets from a manager (or at least env-injected, never
  committed). Keep the **config hash in provenance** (P0).
- Separate "model parameters" (vols, recovery, fallbacks — governed, change-controlled) from
  "runtime config" (paths, parallelism, log level).

**Benefit.** Fail-fast on misconfig, safe secret handling, clean promotion across environments.
**Effort: S–M.**

---

## 4. Target Architecture & Roadmap

### 4.1 Target shape

```
            ┌───────────────────────────────────────────────────────────────┐
            │ Market Data Plane                                             │
  vendors ─►│  ingest → validate (schema + quality gate) → IMMUTABLE        │
            │  point-in-time SNAPSHOT (content-addressed id) ──────────────┐│
            └───────────────────────────────────────────────────────────────┘│
                                                                              │ snapshot_id
   ┌──────────────────────────────────────────────────────────────────────┐  │
   │ Pricing & Risk Plane (one engine, two drivers)                        │◄─┘
   │                                                                        │
   │   PricingEngine.price(trade, snapshot, ctx)  ── pure, stateless ──┐    │
   │     • shared curve/vol cache keyed by snapshot_id                 │    │
   │     • parallel across trades (process workers / Ray / K8s shards) │    │
   │                                                                   │    │
   │   Risk: bump-and-revalue sensitivities · scenario/stress · VaR ───┘    │
   │                                                                        │
   │   Driver A: EOD batch (K8s CronJob / Airflow)  — system of record      │
   │   Driver B: on-demand service (FastAPI/gRPC)   — what-if / intraday    │
   └───────────────────────────┬────────────────────────────────────────────┘
                               │ results + provenance (run_id, git_sha, snapshot_id, versions)
                               ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │ Results Plane:  Parquet/DuckDB (+ DB of record) · results API         │
   │ Observability:  structured logs · Prometheus metrics · OTel traces ·  │
   │                 dashboards · PV-drift & staleness alerts               │
   └──────────────────────────────────────────────────────────────────────┘
```

### 4.2 Phased roadmap

| Phase | Items | Theme | Outcome |
|---|---|---|---|
| **1 — Foundations** | P0, P0b, P0c, P0d | Reproducibility, data gating, reproducible builds | Every price is auditable; bad data is blocked; the build is deployable and tested. *Do this first — everything else leans on it.* |
| **2 — Scale & serve** | P1, P1b, P1c | Parallel engine, service interface, results store | Throughput for large books + scenario fan-out; on-demand pricing; queryable history. |
| **3 — Risk** | P2 | Sensitivities → scenarios → VaR → P&L explain | A risk system, not just a pricer; the weekly-stress use case is realized. |
| **4 — Operate & govern** | P3, P4, P5 | Observability, model governance, config/secrets | The system is visible, alertable, defensible, and safely configurable. |

### 4.3 Guiding principles for the build

- **Don't disturb the good core.** The input/loader/ql layering and declarative requirements stay;
  most items are additive layers (provenance, snapshot, cache, risk) over the existing engine.
- **One engine, many drivers.** Batch and service must call identical pricing code — never fork the
  numerics, or batch and intraday will disagree.
- **Reproducibility is a feature, not paperwork.** Snapshot ID + pinned versions + config hash on
  every number is what makes scaling, risk, and audit all tractable. It's cheap; do it first.
- **Process-isolate the global eval date.** Any parallelism must respect that
  `ql.Settings.evaluationDate` is process-global — one valuation date per process.

---

## 5. Quick-Reference: Priority × Effort × Value

| Pri | Item | Effort | Value | Why this rank |
|---|---|---|---|---|
| P0 | Provenance on every result | S | High | Cheap; unlocks audit + everything downstream |
| P0b | Immutable PIT snapshots + ID | M | High | Reproducibility + substrate for risk/scale |
| P0c | Data quality gate + schema contracts | M | High | Stops silent pricing on bad/stale data |
| P0d | Pin deps, containerize, CI | M | High | Numerically stable, deployable, regression-safe |
| P1 | Parallel engine + shared curve cache | M | High | Throughput for book + scenario fan-out |
| P1b | Engine/driver split + service API | M–L | High | On-demand pricing; one code path |
| P1c | Results store + query/API | M | Med-High | History, aggregation, P&L explain |
| P2 | Risk engine (greeks/stress/VaR) | L | High | Biggest *functional* gap; realizes "Risk" |
| P3 | Observability (logs/metrics/traces/alerts) | M | Med-High | See, prove, and get paged |
| P4 | Model governance + regression gates | M | Med | Prices can't silently drift |
| P5 | Config validation + secrets + envs | S–M | Med | Fail-fast, safe promotion |

---

## 6. Implementation status & pending work

**Delivered (P0–P5, all on `main`):** P0 provenance · P0b immutable point-in-time market-data
snapshots · P0c data-quality gate + schema contracts · P0d pinned deps / Dockerfile / K8s / CI ·
P1 shared curve cache + `--workers` parallelism · P1b `PricingEngine` + FastAPI service ·
P1c Parquet/DuckDB results store + query API · P2 risk engine (sensitivities, stress scenarios,
sensitivity- **and** full-revaluation historical VaR/ES, P&L attribution) · P3 observability
(JSON logs, Prometheus metrics, run manifest, PV-drift) · P4 model governance (golden-PV gate, IPV,
model inventory) · P5 config validation (pydantic) + runtime settings + env overlay.
Plus **portfolio/trade-economics versioning** (content-addressed `portfolio_version`, governed
`PortfolioStore`, stamped into provenance + the run manifest) — the P1c deferral, now closed.

**Pending / future work (tracked TODOs):**

- [ ] **CI data provisioning.** The real-data tests (golden-PV gate, all `*_real_data` tests) need
  `data/marketdata`, which is gitignored (too large for GitHub), so the GitHub Actions workflow is
  currently **manual-only** (`workflow_dispatch`). To restore automatic CI: provision the market
  data in the runner — a committed small **fixture snapshot** (a few CSVs sufficient for a reduced
  golden set) or a **DVC / object-store pull** step — then re-enable the `push:`/`pull_request:`
  triggers in `.github/workflows/pricer-ci.yml`. Until then, lint + type-check can run in CI
  unconditionally (they need no data); only the test job needs the data.
- [ ] **Grafana dashboards** over the P3 metrics. The pipeline already exposes Prometheus metrics
  (service `GET /metrics`; batch writes an OpenMetrics textfile) and a per-run manifest. Build
  dashboards for: run health (success/blocked/failed counts, run duration, last-run age), pricing
  latency by asset class, data-quality / PV-drift alert rates, and book PV over time. Deliverable is
  Grafana dashboard JSON + a Prometheus scrape config (infra, not application code), plus alert
  rules (e.g., EOD-not-completed, PV-drift-spike, stale-data-block).
- [ ] **Full multiprocess Prometheus** for `--workers>1` (per-trade counters/latency currently live
  in worker processes; run-level metrics are aggregated correctly in the parent).
- [x] **Risk extras** — parametric VaR (`risk/var.py`), VaR backtesting (`risk/backtest.py`,
  Kupiec + Christoffersen), and a limits framework (`risk/limits.py`) are now **delivered** (plus
  Filtered HS, Stressed VaR, component-ES, JTD/IDR). Only **FRTB-SA / ISDA-SIMM capital** remains
  de-scoped.

---

*This review is intentionally additive: the existing `src/pricer` layering, declarative
market-data requirements, and multi-curve-ready interface are the right foundation and are
preserved throughout. The work is to wrap that solid engine in the reproducibility, scale, risk,
and operability a production valuation & risk platform requires.*
