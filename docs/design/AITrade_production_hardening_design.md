# Production Hardening & Operability — Cross-Module Target-State Design

Author: Architecture review (Claude)
Date: 2026-06-21
Scope: Make all four sub-systems (`marketdata`, `pricer`, `risk`, `scenariogen`) deployable,
supportable, and monitorable as a **small production setup (< 50 users)**. This document is the
agreed backlog from the design review; it is forward-looking and does **not** change pricing/risk
math or portfolio scope.

> **How to read this.** §0 records assessment findings and corrections. §1 states the deployment
> shape and guiding constraints. §2 defines the shared platform package (`src/common/`). §3 is the
> per-item design (items 1–10), each with *Problem → Design → Interfaces → Files touched →
> Acceptance*. §4 is the dependency/sequencing graph. §5 lists non-goals and risks. File references
> are clickable.

---

## 0. Assessment summary & corrections

Four modules were reviewed against the actual code.

| Module | Maturity | One-line |
|--------|----------|----------|
| `pricer` | **High** (~80% prod-ready) | Layered engine/runner, `obs/`, governance, provenance, Parquet+DuckDB store, Docker+K8s. **Defines the platform standard.** |
| `scenariogen` | **Medium-high** | Feature-complete, content-addressed versioning, 3-tier governance gate, atomic I/O. Thin on runtime metrics; MacroRAG client lacks resilience. |
| `risk` | **Medium-high** | Feature-complete sensitivities/VaR/scenarios/P&L-explain. Text logging only; full-reval VaR sequential; factor history not point-in-time. |
| `marketdata` | **Low-medium** | Solid HTTP client + idempotent upsert, but **excluded from CI/lint/type**, no tests, no metrics, no data provenance. The boundary with unreliable external sources — needs hardening *most*. |

**Correction (important):** The initial review flagged `.env` as committed to VCS. This is **false** —
`.env` is gitignored ([.gitignore:8](../../.gitignore#L8)) and untracked. There is **no credential
exposure in git history and no key rotation required.** Secrets handling is fine for local/dev. The
only forward-looking secrets item is operational: when deploying to K8s, inject via K8s Secrets
rather than mounting a `.env`. This is folded into item 2 (config/settings), not a standalone
security item.

**Highest-leverage insight:** `pricer` already contains a complete observability/provenance/config
toolkit. The cheapest large win is to **factor it into a shared package and adopt it in the other
three modules**, giving one uniform operational picture for little new code. Items 1–4 are that work.

---

## 1. Deployment shape & guiding constraints

For < 50 users we explicitly avoid HA clusters, multi-region, and DB servers. Target shape:

```
            ┌──────────── Orchestrator (dependency-aware DAG) ────────────┐
  feeds →  marketdata → [snapshot + DQ gate] → pricer → risk → scenariogen
              (jobs)        (immutable PIT)    (batch+API) (batch)  (batch+signoff)
                 │               │                 │         │          │
                 └───────────────┴────────┬────────┴─────────┴──────────┘
                                          ▼
        Shared platform (src/common): obs (metrics/logs/traces) · config/settings ·
        provenance/run-manifest · Parquet+DuckDB store · DQ gate
                                          ▼
        External: Prometheus + Grafana + Loki + Alertmanager | API gateway (TLS+auth)
```

**Constraints that shape every decision below:**

- **Serverless data tier.** Keep the Parquet + DuckDB pattern ([result_store.py](../../src/pricer/output/result_store.py)).
  No Postgres/ClickHouse. Single-writer batch jobs; readers query Parquet globs.
- **One image, many entrypoints.** The same SHA-tagged container runs every job; behaviour is
  switched by entrypoint + `PRICER_ENV` env, never by rebuild ([Dockerfile](../../infra/pricer/Dockerfile)).
- **Pull-based metrics.** Prometheus scrapes a textfile (batch) or `/metrics` (service). No push
  gateway unless a job is too short-lived to scrape.
- **Additive, not rewrites.** Every change builds on the existing 3-layer pricer design and the
  module runners. No behavioural change to pricing/risk numbers.
- **Single shared package** `src/common/` is introduced; the three top-level packages
  (`src/marketdata`, `src/pricer`, `src/scenariogen`) depend on it. `common` depends on nothing
  internal (no import cycles).

---

## 2. The shared platform package: `src/common/`

New top-level package. Everything cross-cutting moves here so all three product packages share one
implementation. Proposed layout:

```
src/common/
  __init__.py
  obs/                # item 1 — moved from src/pricer/obs
    __init__.py
    log.py            # JSON logging + contextvars run context
    metrics.py        # Prometheus registry + helpers + textfile writer
    tracing.py        # observe_duration() span/timer (OTel optional)
    run_manifest.py   # per-run audit row → Parquet/DuckDB
    drift.py          # value-drift detection (generalized from PV drift)
  config/             # item 2
    __init__.py
    loader.py         # JSON base + <ENV> overlay deep-merge (from pricer/config.py)
    settings.py       # pydantic-settings base (env/secrets), one per module subclass
    models.py         # shared pydantic config fragments (obs, data_quality)
  provenance.py       # item 3 — RunProvenance (from src/pricer/provenance.py)
  store/              # item 8/C8 helpers
    __init__.py
    parquet_store.py  # shared Hive-partition write + DuckDB read base
    retention.py      # retention/GC policy applied to any store root
  quality/            # item 5
    __init__.py
    gate.py           # schema/rowcount/coverage/null/staleness checks
```

**Migration rule:** `src/pricer/obs/*`, `src/pricer/provenance.py`, and the config trio become thin
re-export shims (`from common.obs.metrics import *`) for one release so existing pricer imports keep
working, then are deleted. This keeps the 198-test pricer suite green during the move.

**Dependency note:** `common.obs.run_manifest` and `common.store` pull in `pyarrow`+`duckdb`. `pricer`
already has these; `marketdata` does not. To avoid forcing heavy deps on the lightweight downloaders,
`run_manifest` import is lazy and the Parquet writer degrades to a JSONL append if pyarrow is absent
(marketdata can opt into JSONL manifests). `obs.log`/`obs.metrics` depend only on stdlib +
`prometheus-client` (already a dep).

---

## 3. Per-item design

### Item 1 — Shared observability SDK  ★ foundation

**Problem.** `pricer` has structured JSON logging, Prometheus metrics, lightweight tracing, and
run-manifests ([src/pricer/obs/](../../src/pricer/obs/)). `risk` and `scenariogen` log plain text only;
`marketdata` has baseline logging and **zero metrics**. No uniform way to answer "did the job run,
how long, did it fail, is data stale" across modules.

**Design.** Move `src/pricer/obs/` → `src/common/obs/` unchanged in behaviour, generalize three
spots, then adopt in every entrypoint.

- `log.configure_logging(json_logs, level, logfile)` + `bind()`/`log_context()` already
  contextvar-based ([log.py](../../src/pricer/obs/log.py)) — module-agnostic already.
- `metrics.py`: keep the pricer metric families; add a small **namespaced factory** so each module
  registers its own metrics on the shared `REGISTRY` without name clashes:
  ```python
  # common/obs/metrics.py
  def counter(module: str, name: str, doc: str, labels: tuple[str, ...]) -> Counter: ...
  def histogram(module: str, name: str, doc: str, labels, buckets) -> Histogram: ...
  def write_textfile(path: str) -> None   # OpenMetrics dump for node_exporter
  # metric name = f"{module}_{name}" → marketdata_source_rows_total, risk_var_value, ...
  ```
- `drift.py`: generalize `PV drift` → `detect_drift(prev, curr, abs_tol, rel_tol)` reusable by risk
  (VaR jumps) and marketdata (row-count drops).
- `run_manifest.py`: already generic (run_id, git_sha, status, counts, duration). Each module writes
  one row per run to `data/<module>/store/runs/`.

**Adoption (each module's runner):** wrap the run in `configure_logging(...)` + `log_context(run_id=…)`,
emit a `RunManifest` row at the end, and instrument the hot path with `observe_duration(...)`.

**Files touched.** New `src/common/obs/*` (moved); shims in `src/pricer/obs/*`; edits to
[src/marketdata/*/runner.py](../../src/marketdata/) (8), [src/pricer/risk_runner.py](../../src/pricer/risk_runner.py),
[src/scenariogen/runner.py](../../src/scenariogen/runner.py) + `signoff_runner.py`.

**Acceptance.** Every module emits JSON logs with `run_id`; `/metrics` (service) and textfiles
(batch) expose per-module metrics; a `runs` DuckDB query returns rows for all four modules; pricer's
existing tests stay green via shims.

---

### Item 2 — Shared config validation + env overlay

**Problem.** `pricer` has pydantic-validated config + `<ENV>` overlay + `PRICER_*` settings
([config.py](../../src/pricer/config.py), [config_models.py](../../src/pricer/config_models.py),
[settings.py](../../src/pricer/settings.py)). `scenariogen` has typed-ish JSON configs;
`marketdata` parses **raw JSON with no schema** ([config_loader.py](../../src/marketdata/common/config_loader.py)) —
a missing field fails at runtime as `AttributeError`, not at load.

**Design.**
- Move pricer's loader/merge into `common/config/loader.py` (`load(base_path, env) -> dict` with
  recursive overlay merge + `"today"` date resolution, which marketdata already needs).
- `common/config/settings.py`: a `BaseSettings` subclass with shared fields (`ENV`, `JSON_LOGS`,
  `LOG_LEVEL`, `WORKERS`) and `SecretStr` for keys. Each module subclasses to add its own
  (`MarketdataSettings.FRED_API_KEY`, etc.). **This is where K8s-Secrets injection lands** —
  settings read from env, so a K8s Secret mounted as env vars "just works"; no `.env` in prod.
- `common/config/models.py`: shared pydantic fragments (`ObservabilityConfig`, `DataQualityConfig`)
  reused by every module's config model.
- **marketdata gets pydantic models** for `configs/marketdata/*/*.json`: a `SourceConfig`
  (enabled, base_url, auth, timeout_s, retry, endpoint, output_file) and `AssetClassConfig`. Fail
  fast with a clear `ConfigError`.

**Files touched.** New `src/common/config/*`; new `src/marketdata/common/config_models.py`;
edits to `config_loader.py`, the three module config modules; settings subclasses per module.

**Acceptance.** All four modules load config through the common loader; an invalid marketdata config
raises `ConfigError` at startup naming the bad field; secrets resolve from env with no plaintext in
logs (`SecretStr`).

---

### Item 3 — Provenance + run-manifest for every module

**Problem.** `pricer` stamps `RunProvenance` (run_id, git_sha, config_hash, quantlib/python version,
priced_at) into every result ([provenance.py](../../src/pricer/provenance.py)). `risk` reuses it;
`scenariogen` has its own content-addressed versioning; `marketdata` has **none** — CSVs carry no
"who/when/from where".

**Design.**
- Move `provenance.py` → `common/provenance.py` (already module-agnostic). Add optional
  `data_snapshot_id` field (used by item 4).
- Every module captures `RunProvenance.capture(root, config)` at run start and writes it into its
  run-manifest row (item 1) and its output artifacts.
- **marketdata data provenance:** extend [data_store.py](../../src/marketdata/common/data_store.py)
  `upsert_csv()` to also write a sidecar `<output>.provenance.json` per file:
  `{source_url, fetched_at, git_sha, config_hash, row_count, content_sha256, date_range}`. Optionally
  add a `_fetched_at` column to rows (config-gated to avoid churning existing schemas).

**Files touched.** Moved `common/provenance.py`; shim at `src/pricer/provenance.py`;
edits to `data_store.py` + each marketdata downloader's write path; manifest wiring from item 1.

**Acceptance.** Every output artifact (CSV, result JSON, scenario JSON) is traceable to a git_sha +
config_hash + timestamp; a single DuckDB query over `runs/` joins provenance across all modules.

---

### Item 4 — Immutable PIT snapshot + lineage

**Problem.** Reproducibility has gaps: pricer's `market_data_snapshot_id` is **`None`** today
([provenance.py] ResultProvenance), so results don't pin the exact data they used; risk's
`FactorHistory` recomputes factors live from **mutable** CSVs ([factor_history.py](../../src/pricer/risk/factor_history.py)) —
a later CSV edit silently changes historical VaR retros; scenariogen's covariance inherits the same.

**Design.**
- **Snapshot id:** after marketdata + DQ gate, compute a content hash over the resolved market-data
  set and persist an immutable snapshot manifest `data/marketdata/snapshots/<snapshot_id>.json`
  (file list + per-file content_sha256 from item 3). The pricer loader
  ([snapshot.py](../../src/pricer/loader/snapshot.py)) stamps this `snapshot_id` into
  `ResultProvenance` (the field already exists — just wire it).
- **PIT factor history:** `FactorHistory` reads via the snapshot manifest (or a frozen copy under
  `snapshots/<id>/`) rather than the live CSV, so a VaR run is pinned to a snapshot. Backfills become
  reproducible: "re-run risk for date D against snapshot S".
- **Lineage thread:** `snapshot_id` flows marketdata → pricer → risk → scenariogen, recorded in
  every run-manifest. One column links a scenario P&L back to the exact bytes of input data.

**Files touched.** New `src/common/store/parquet_store.py` snapshot helper (or a small
`snapshot_manifest.py`); edits to pricer `snapshot.py`, risk `factor_history.py`, scenariogen
`covariance.py`; marketdata snapshot writer step.

**Acceptance.** A priced result names a non-null `snapshot_id`; re-running risk against a pinned
snapshot reproduces VaR bit-for-bit; editing a source CSV after snapshot does **not** change a
pinned retro.

---

### Item 5 — Data-quality gate immediately after ingest

**Problem.** `pricer` has a staleness/quality policy applied **at pricing time**
([quality.py](../../src/pricer/input/common/quality.py), [staleness.py](../../src/pricer/loader/staleness.py)).
There is no gate **right after marketdata** — a download that returns 0 rows or a broken schema is
only noticed when pricing warns/blocks much later.

**Design.**
- Move the staleness/quality primitives into `common/quality/gate.py` and generalize to a
  `DataQualityGate` that runs as an **orchestration step between marketdata and snapshot**:
  per-file checks for schema (expected columns), row count ≥ floor, date coverage (no gap > N days),
  null fraction ≤ threshold, staleness ≤ max days. Driven by `DataQualityConfig` (item 2).
- Outcome: `warn | block` per check (reuse pricer's action vocabulary). `block` fails the DAG before
  a bad snapshot is created; emits `marketdata_dq_total{severity}` metric (item 1) + structured log.
- Reuses item 3 provenance (`row_count`, `date_range`) so most checks are cheap metadata reads.

**Files touched.** New `src/common/quality/gate.py` (lift from pricer input/common); new
`scripts/marketdata/dq_gate.py` runnable step (automates the manual `verify_*.py`); config in
`configs/marketdata/*` + shared `DataQualityConfig`.

**Acceptance.** A zero-row or schema-broken download blocks the DAG with a named failing check; the
existing pricing-time quality policy is unchanged (now sharing the same code).

---

### Item 6 — marketdata tests + bring into CI/lint/type gate

**Problem.** marketdata is **explicitly excluded** from ruff/mypy/pytest
([pyproject.toml:11](../../pyproject.toml#L11), [pyproject.toml:50](../../pyproject.toml#L50)) and has **no
tests**. It's the module that talks to the flakiest dependencies (scrapers, third-party APIs) — it
needs tests most.

**Design.**
- Add `tests/marketdata/` with unit tests using a **fake HTTP transport** (mirror scenariogen's
  `MacroTransport` protocol seam — [macro/client.py](../../src/scenariogen/macro/client.py)). Refactor
  `http_client.py` minimally to accept an injectable transport so no real network in tests.
- Cover: retry/backoff/throttle logic, auth-type header/param construction, `upsert_csv` merge/dedup
  ([data_store.py](../../src/marketdata/common/data_store.py)), config-model validation (item 2),
  provenance sidecar (item 3), per-downloader parse of a captured fixture response.
- Remove marketdata from the `extend-exclude`/mypy-ignore lists; fix the type/lint fallout. Add a
  `marketdata` job (or extend the existing) in [pricer-ci.yml](../../.github/workflows/pricer-ci.yml).

**Files touched.** `pyproject.toml` (remove exclusions), new `tests/marketdata/*`, small DI refactor
in `http_client.py`, CI yaml.

**Acceptance.** `ruff` + `mypy --strict` + `pytest` green including marketdata; CI runs marketdata
tests on every push; no real network calls in the suite.

---

### Item 7 — risk: parallelism, missing-factor alerting, backtest + limits

**Problem.** Three gaps in [src/pricer/risk/](../../src/pricer/risk/):
1. **Full-reval VaR is sequential** — `O(n_days × n_trades)` repricings (~10–20 min for a 250-trade
   book) in a single `curve_cache()` context ([historical_var.py](../../src/pricer/risk/historical_var.py)).
2. **Missing factor files degrade silently** — a warning, then VaR computed on fewer factors
   ([var.py](../../src/pricer/risk/var.py)).
3. **No VaR backtesting and no limit monitoring** — risk output is a standalone JSON island.

**Design.**
- **Parallelize full-reval VaR** by reusing the pricer `ProcessPoolExecutor` pattern from
  [runner.py](../../src/pricer/runner.py) (`_worker_init`/`_worker_price_chunk`). Fan out over historical
  days (each day independent); each worker keeps its own process-local `curve_cache()`. Gated by the
  same `--workers` / `PRICER_WORKERS` knob.
- **Missing-factor visibility:** emit `risk_factor_missing_total{factor}` metric + a `block`-able DQ
  check (item 5 vocabulary) so a dropped factor pages instead of silently shrinking VaR.
- **Backtesting:** new `risk/backtest.py` — rolling 1-day VaR forecast vs realized next-day book
  P&L (from the result store), Kupiec/traffic-light exception counts, written to the store and
  charted. Foundation for model validation/governance sign-off.
- **Limit monitoring:** new `risk/limits.py` reading limit config (mirror scenariogen's
  `limits.json`), compare book VaR/sensitivities to limits, emit `risk_limit_breach_total` →
  Alertmanager (item 10).

**Files touched.** `historical_var.py` (worker fan-out), `var.py`/`factor_history.py`
(missing-factor metric), new `risk/backtest.py`, `risk/limits.py`, `risk_runner.py` wiring;
`configs/pricer/pricer_config.json` (limits, backtest window).

**Acceptance.** Full-reval VaR scales with `--workers` (measured speedup); a missing factor file
raises a metric/alert (configurably blocks); backtest exception report persisted; a limit breach
fires an alert.

---

### Item 8 — scenariogen: obs metrics, MacroRAG resilience, real limits, retention

**Problem.** [src/scenariogen/](../../src/scenariogen/) is feature-complete but operationally thin:
text logs only; the MacroRAG client has **no retry/backoff/circuit-breaker** and ignores
`Retry-After`/429 ([macro/client.py](../../src/scenariogen/macro/client.py), a 30s timeout fails
straight to silent static fallback); `limits.json` ships **placeholder** 50mm/20mm
([configs/scenariogen/limits.json](../../configs/scenariogen/limits.json)); version/result/sign-off
stores **grow unbounded**.

**Design.**
- **Obs metrics (item 1):** `scenariogen_scenarios_total`, `scenariogen_macro_gate_pass_ratio`,
  `scenariogen_plausibility_flags_total`, `scenariogen_limit_breach_total` → Grafana + alerts.
- **MacroRAG resilience:** add retry-with-backoff (reuse marketdata `http_client`'s retry semantics —
  another reason to make it injectable in item 6), honor `Retry-After` on 429, a startup `/healthz`
  check, and a circuit breaker. **The static fallback must alert, not be invisible** — emit
  `scenariogen_macro_fallback_total` when it triggers ([provider.py](../../src/scenariogen/macro/provider.py)).
- **Real limits:** replace placeholders in `limits.json` with the desk's actual loss/capital limits
  (config-only; flagged as a go-live checklist item, value supplied by the user).
- **Retention/GC:** `common/store/retention.py` policy (item 2 config) applied to scenario
  `versions/`, `manifest/`, `scenario_results/`, `signoff/` — keep N days/versions, archive older.

**Files touched.** `runner.py`/`signoff_runner.py` (metrics), `macro/client.py`+`provider.py`
(resilience), `configs/scenariogen/limits.json` (values), new `common/store/retention.py` + a GC
step; reuse shared `http_client`.

**Acceptance.** Scenario metrics visible in Grafana; a MacroRAG 429/timeout retries then alerts on
fallback (no longer silent); limits are real values; a retention job prunes/archives old store
partitions.

---

### Item 9 — API gateway: TLS + auth + rate limit

> **Update (2026-07-16): the in-app `X-API-Key` auth is DELIVERED** (`PRICER_API_KEY` +
> `Settings.api_key` + middleware on `service.py`, no-op when unset). TLS + rate limiting stay at the
> edge. The full deploy realization (containers, S3, ALB, IAM) is in
> [AITrade_aws_deployment_design.md](AITrade_aws_deployment_design.md); items 9–10's `infra/pricer`
> K8s/PVC plan is superseded by it for AWS.

**Problem.** The FastAPI service ([service.py](../../src/pricer/service.py)) exposes ~14 endpoints
(pricing, results, risk, governance) with **no auth, no TLS, no rate limiting** — fine for localhost,
unacceptable for any network exposure.

**Design.** Keep the service stateless and put policy at the edge.
- **Reverse proxy** (NGINX/Traefik/Envoy) terminating TLS and enforcing **API-key or OIDC** + rate
  limiting. For < 50 users a static API-key header checked by the proxy (or a thin FastAPI
  dependency) is sufficient; OIDC if SSO is desired.
- **Thin in-app fallback:** a FastAPI dependency that validates an `X-API-Key` against a `SecretStr`
  from settings (item 2), so the app is safe even if deployed without the proxy. Add a per-IP rate
  limiter (`slowapi`) as defense in depth.
- `/health` and `/metrics` stay unauthenticated (scrape/liveness) but bind to the internal network.

**Files touched.** `service.py` (auth dependency, rate-limit middleware), new
`infra/pricer/gateway/` (proxy config), settings field for the API key.

**Acceptance.** Unauthenticated request → 401; valid key → 200; burst beyond limit → 429; TLS
terminated at the proxy; `/health` + `/metrics` reachable for ops only.

---

### Item 10 — Orchestration, monitoring stack, Helm, backup/retention

**Problem.** Only `pricer` has a scheduled artifact ([eod-cronjob.yaml](../../infra/pricer/k8s/eod-cronjob.yaml));
there's no inter-job dependency, no metrics/alerting stack, raw per-module YAML, and no backup.

**Design.**
- **Orchestration DAG:** dependency-aware scheduler running
  `marketdata → dq_gate → snapshot → pricer → risk → scenariogen` with retries, exit-code
  propagation, and on-failure alerts. Prefect/Dagster (Python-native, light) preferred over Airflow
  for this scale; or chained Argo Workflows if staying pure-K8s.
- **Monitoring stack:** single-node Prometheus (scrapes textfiles + service `/metrics`) + Grafana
  (dashboards per §"monitoring contract") + Loki (JSON log aggregation from item 1) + Alertmanager
  (the common alert taxonomy below) → Slack/email.
- **Helm chart** `infra/helm/` parameterizing image/registry/schedule/resources/secrets for all jobs
  + the service; replaces per-module YAML drift.
- **Backup/retention:** nightly backup of the `data/` volume (or object-store sync) + the
  retention/GC policy from items 2/8.

**Monitoring contract (alert taxonomy):**

| Source | Alert on |
|--------|----------|
| marketdata | source failed / 0 rows / stale > threshold / schema changed |
| dq gate | any `block` check failed |
| pricer | run failed / trades blocked / PV drift > tol / no EOD run by SLA |
| risk | factor missing / VaR jump > tol / runtime SLA / **limit breach** |
| scenariogen | **limit breach** / macro fell back / GATE failures / sign-off missing |
| platform | any job failed / API 5xx / disk > 80% / backup failed |

**Files touched.** New `infra/orchestration/` (DAG defs), `infra/monitoring/` (Prometheus/Grafana/
Loki/Alertmanager config + dashboards), `infra/helm/`, backup CronJob.

**Acceptance.** One DAG run executes the full pipeline with correct ordering and retries; Grafana
shows per-module dashboards; a forced failure pages via Alertmanager; `helm install` deploys the
whole stack; a restore-from-backup is verified once.

---

## 4. Sequencing & dependency graph

Items 1–4 are the shared foundation and must land first (in order); 5–8 are per-module and can
proceed in parallel once the foundation exists; 9–10 are infra and can start anytime but finalize
last (they consume the metrics/manifests from 1).

```
  1 (obs) ──► 2 (config) ──► 3 (provenance) ──► 4 (PIT snapshot)
                  │                │                  │
                  └──────► 5 (DQ gate) ◄─────────────┘
  1 ──► 6 (marketdata tests/CI)         [needs http_client DI; unblocks 8's retry reuse]
  1,3 ─► 7 (risk perf/backtest/limits)
  1,6 ─► 8 (scenariogen obs/macro/retention)
  2 ──► 9 (API auth)
  1,3,5,7,8 ─► 10 (orchestration/monitoring/helm/backup)
```

**Recommended execution order:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 (matches the numbering). Each
of 1–8 ships independently with green tests; 9–10 are deploy artifacts validated in a staging
namespace.

**Per-item definition of done:** code + tests green (`ruff`+`mypy --strict`+`pytest`); shims keep
prior imports working until removed; no change to pricing/risk numbers (governance golden-PV gate
must still pass).

---

## 5. Non-goals & risks

**Non-goals (out of scope for < 50 users):** HA/multi-region, a database server, microservice split,
distributed tracing backend beyond optional OTel export, autoscaling, multi-tenant auth. Pricing,
risk, and scenario **math is unchanged** — this is purely operability.

**Risks & mitigations:**
- *Import cycles / churn from the `common/` move.* → one-release re-export shims; move behind green
  tests; delete shims only after all imports updated.
- *Forcing pyarrow/duckdb onto lightweight marketdata.* → lazy import; JSONL fallback for manifests.
- *marketdata entering the strict gate surfaces latent type errors.* → expect a fix-up pass in item 6;
  do it in isolation so it doesn't block other items.
- *PIT snapshot freezing storage growth.* → covered by retention (items 2/8); snapshots store hashes +
  manifests, not full data copies, unless a frozen copy is explicitly requested for a backfill.
- *MacroRAG endpoint still not live.* → resilience work (item 8) is validated against the existing
  fake transport; real integration is a separate go-live gate.

---

## 6. Go-live checklist (config/ops values the user must supply)

- Real `configs/scenariogen/limits.json` loss/capital limits (item 8).
- Real risk limit config (item 7).
- API key(s) / OIDC provider for the gateway (item 9).
- K8s Secrets for source API keys (FRED/Stooq/FINRA/EIA) (item 2).
- Alertmanager destination (Slack webhook / email) (item 10).
- Backup target (volume snapshot or object-store bucket) (item 10).
- EOD SLA time + staleness thresholds per source (items 5/10).
