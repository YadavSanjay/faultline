# Deployment & Operations Design — API Gateway, Orchestration, Monitoring (items 9–10)

Author: Architecture review (Claude)
Date: 2026-06-21
Scope: **Design-only.** The deploy/infra layer that runs *outside* the code gate — securing the
read API (item 9) and operating the pipeline (item 10: orchestration, monitoring, Helm, backup).
Items 1–8 (the in-repo code: shared `src/common/`, observability, provenance, PIT snapshots, DQ
gate, marketdata-in-gate, risk perf/backtest/limits, scenariogen resilience/retention) are
**complete and merged**; this document describes how to operate what they emit. Actual values,
cluster, and tool choices are environment-specific and deferred — this is the blueprint, not the
manifests.

> This complements `AITrade_production_hardening_design.md` (the items 1–10 backlog). It expands
> items 9–10 only, and is grounded in the signals/artifacts the merged code already produces.

---

## 0. What's already in place (the inputs to this layer)

The operations layer doesn't need new instrumentation — items 1–8 already emit everything it
consumes. The job here is to *route, secure, scrape, alert, schedule, and back up* it.

| Asset (already merged) | Where | Used by |
|---|---|---|
| Prometheus metrics (per module) | service `GET /metrics`; batch OpenMetrics textfile (`observability.metrics_textfile`) | §3 monitoring |
| Structured JSON logs (run_id context) | stdout + `logs/*.log` ([common/obs/log.py](../../src/common/obs/log.py)) | §3 Loki |
| Per-run manifests | `data/<module>/store/runs/` (Parquet/DuckDB) | §3 audit, §2 DAG status |
| PIT snapshots + lineage | `data/marketdata/snapshots/`, `data/<module>/snapshots/` | §3 reproducibility |
| DQ gate (exit-coded) | [scripts/marketdata/dq_gate.py](../../scripts/marketdata/dq_gate.py) | §2 DAG gate |
| FastAPI read API | [src/pricer/service.py](../../src/pricer/service.py) (~14 endpoints) | §1 gateway |
| EOD CronJob + image | [infra/pricer/k8s/](../../infra/pricer/k8s/), [Dockerfile](../../infra/pricer/Dockerfile) | §2/§4 |
| Retention/GC | [scripts/scenariogen/gc.py](../../scripts/scenariogen/gc.py), [common/store/retention.py](../../src/common/store/retention.py) | §5 backup/retention |

**Metric inventory** (names are fixed by the merged code — dashboards/alerts bind to these):

```
pricer_trades_total{asset_class,status}     pricer_run_seconds        pricer_book_pv{valuation_type}
pricer_price_seconds{asset_class}           pricer_run_trades{status} pricer_last_run_timestamp_seconds
pricer_data_quality_total{severity}
marketdata_runs_total{asset_class,status}   marketdata_run_seconds{asset_class}
marketdata_source_total{asset_class,source,status}                    marketdata_dq_total{severity}
risk_runs_total{status}    risk_run_seconds  risk_var_value{level}    risk_sensitivities_failed_total
risk_factor_missing_total{factor}           risk_limit_breach_total{limit}
scenariogen_runs_total{status}  scenariogen_run_seconds  scenariogen_scenarios_total{outcome}
scenariogen_limit_breach_total              scenariogen_macro_fallback_total{reason}
```

---

## 1. Target topology

```
                        Internet / corp LAN
                                │  TLS
                    ┌───────────▼────────────┐
                    │  Reverse proxy / gateway │  TLS term · authn · rate-limit
                    │  (NGINX / Traefik / Envoy)│
                    └───────────┬────────────┘
                       authenticated │  (internal network only)
                    ┌───────────▼────────────┐        ┌───────────────┐
                    │  pricer FastAPI service  │  /metrics ◄── Prometheus ──► Grafana
                    │  (stateless, N replicas) │        │  + Alertmanager │
                    └───────────┬────────────┘        └──────┬────────┘
                                │ reads                        │ alerts
                    ┌───────────▼───────────────────────┐     ▼
                    │  data/  (Parquet + DuckDB, RWX vol) │   Slack / email / PagerDuty
                    └───────────▲───────────────────────┘
                                │ writes (batch)
        ┌───────── Orchestrator DAG (Prefect / Dagster / Argo) ─────────┐
        │ marketdata → dq_gate → snapshot → pricer → risk → scenariogen │
        └───────────────────────────────────────────────────────────────┘
                  JSON logs ─────────────► Loki ─────────► Grafana
```

For < 50 users this is a **single-node** Prometheus/Grafana/Loki/Alertmanager + the existing K8s
namespace. No HA, no DB server, no service mesh.

---

## 2. Item 9 — API gateway (TLS + auth + rate limit)

> **Update (2026-07-16): the in-app `X-API-Key` fallback is DELIVERED.** `PRICER_API_KEY` +
> `Settings.api_key` + the middleware on `service.py` (exempting `/health` + `/metrics`, no-op when
> unset) shipped on `main` (AWS design doc Phase D). TLS termination + rate limiting at the edge
> remain gateway/ALB concerns; the AWS realization is in
> [AITrade_aws_deployment_design.md](AITrade_aws_deployment_design.md) (§2, §7.4), which supersedes
> the K8s/PVC framing below for AWS.

**Problem.** `service.py`'s ~14 endpoints (`/price`, `/price-portfolio`, `/results/*`, `/risk/*`,
`/governance/*`, `/runs`, `/portfolio/versions`) have **no auth, no TLS, no rate limit** — fine for
localhost, unacceptable on a network.

**Design — policy at the edge, defense-in-depth in the app.**

1. **Reverse proxy** terminates TLS and enforces auth + rate limiting. For < 50 users a static
   **API key** checked at the proxy is sufficient; **OIDC/JWT** (corp SSO) if SSO is desired. Rate
   limit per key/IP (e.g. 60 req/min) to bound the CPU-heavy pricing endpoints.
2. **In-app fallback** so the service is safe even if deployed without the proxy: a FastAPI
   dependency validating `X-API-Key` against a `SecretStr` from settings, applied to all routers
   except health/metrics. This is the only *code* change in items 9–10 — small, gated, testable:
   ```python
   # sketch — src/pricer/service.py
   from fastapi import Depends, HTTPException, Security
   from fastapi.security import APIKeyHeader
   _api_key = APIKeyHeader(name="X-API-Key", auto_error=False)
   def require_key(k: str | None = Security(_api_key)) -> None:
       expected = get_settings().api_key            # new PRICER_API_KEY SecretStr (item 2 pattern)
       if expected and (k or "") != expected.get_secret_value():
           raise HTTPException(401, "invalid or missing API key")
   # app = FastAPI(dependencies=[Depends(require_key)]) with /health,/metrics exempted
   ```
   If `PRICER_API_KEY` is unset, the dependency is a no-op (dev parity) — so it never breaks local
   use, only enforces when a key is configured.
3. **Per-IP rate limiter** (`slowapi`) as in-app backstop for the proxy limit.

**Endpoint exposure matrix:**

| Endpoint(s) | Auth | Exposure |
|---|---|---|
| `/health` | none | internal (liveness) |
| `/metrics` | none | internal (Prometheus scrape only — netpol-restricted) |
| `/price*`, `/risk/*`, `/results/*`, `/governance/*`, `/runs`, `/portfolio/*` | API key / OIDC | via gateway |

**Acceptance (when implemented).** Unauthenticated → 401; valid key → 200; burst → 429; TLS at the
proxy; `/health` + `/metrics` reachable only on the internal network (NetworkPolicy).

**Files (future).** `service.py` (auth dependency + rate-limit middleware), `settings.py`
(`api_key: SecretStr`), `infra/pricer/gateway/` (proxy config + TLS), a K8s `NetworkPolicy`.

---

## 3. Item 10 — Orchestration DAG

**Problem.** Only `pricer` has a scheduled artifact; there is no inter-job dependency, retry, or
failure propagation across the pipeline.

**Design.** A dependency-aware scheduler runs the daily DAG; each node is the existing entrypoint:

```
marketdata.{8 runners}  ──►  scripts.marketdata.dq_gate  ──►  [snapshot built by pricer]
        │                          (exit 1 = BLOCK → halt)
        └──► (per-source metrics + manifests)
                                   │
   pricer.runner --workers N  ──►  pricer.risk_runner --full-reval-var --workers N  ──►  scenariogen.runner
                                                                                              │
                                                                          scenariogen GC (weekly)
```

- **Tool:** Prefect or Dagster (Python-native, light, good local dev) preferred over Airflow at this
  scale; or **Argo Workflows** to stay pure-K8s (the existing CronJob already implies a cluster).
- **Contracts already in place:** the DQ gate exits non-zero on `block` (hard gate); every job writes
  a run-manifest the DAG can assert on; jobs are idempotent (marketdata upsert, snapshot is
  content-addressed). Retries: marketdata sources retry in-process (HttpClient) + a DAG-level retry;
  pricing/risk are deterministic so a DAG retry is safe.
- **Failure propagation:** DQ `block` or a job non-zero exit fails the DAG run and fires the
  `job_failed` alert (§4). `concurrencyPolicy: Forbid` (already set) prevents overlap.

**Files (future).** `infra/orchestration/` (DAG definition), or extend `infra/pricer/k8s/` with an
Argo `Workflow` + per-step `WorkflowTemplate`s.

---

## 4. Item 10 — Monitoring stack

Single-node **Prometheus + Grafana + Loki + Alertmanager**.

**Scrape config (two modes — both already produced by the code):**
- *Service:* scrape `pricer-svc:8080/metrics` (live registry).
- *Batch jobs:* they exit, so each writes the OpenMetrics **textfile** (`metrics.write_textfile`,
  path from `observability.metrics_textfile`) to a shared dir scraped by the **node_exporter
  textfile collector** (or push to a Pushgateway for very short jobs).

**Grafana dashboards (one per module, bound to the real metric names from §0):**
- *Pipeline overview:* `*_runs_total` by status, `*_run_seconds` (p50/p95), `pricer_last_run_timestamp_seconds` (EOD freshness), DAG success.
- *Market data:* `marketdata_source_total` by `{source,status}`, `marketdata_run_seconds`, `marketdata_dq_total{severity}`.
- *Pricer:* `pricer_trades_total{status}`, `pricer_price_seconds` heatmap, `pricer_book_pv`, `pricer_data_quality_total{severity}` (incl. `drift`).
- *Risk:* `risk_var_value{level}` trend, `risk_factor_missing_total`, `risk_limit_breach_total`, backtest zone (from the run-manifest `backtest_zone`).
- *Scenariogen:* `scenariogen_scenarios_total{outcome}`, `scenariogen_limit_breach_total`, `scenariogen_macro_fallback_total{reason}`.

**Logs:** ship the JSON logs (item 1) to **Loki** (promtail/agent); the `run_id`/`snapshot_id`/
`factor_snapshot_id` fields make a single run greppable end-to-end and joinable to the run-manifest.

**Alertmanager rules (metric → condition → routing):**

| Alert | Expr (sketch) | Severity |
|---|---|---|
| EOD did not run | `time() - pricer_last_run_timestamp_seconds > <SLA>` | page |
| Job failed | `increase(*_runs_total{status="failed"}[1d]) > 0` | page |
| DQ block | `increase(marketdata_dq_total{severity="block"}[1d]) > 0` | page |
| Trades blocked/failed | `increase(pricer_trades_total{status=~"blocked|failed"}[1d]) > 0` | warn |
| PV drift | `increase(pricer_data_quality_total{severity="drift"}[1d]) > 0` | warn |
| Risk factor missing | `increase(risk_factor_missing_total[1d]) > 0` | warn |
| **Risk limit breach** | `increase(risk_limit_breach_total[1d]) > 0` | page |
| **Scenario limit breach** | `increase(scenariogen_limit_breach_total[1d]) > 0` | page |
| Macro fell back | `increase(scenariogen_macro_fallback_total[1d]) > 0` | warn |
| API errors / disk | service 5xx rate; volume > 80% | warn/page |

Routes → Slack (warn) + PagerDuty/email (page). Thresholds (`<SLA>`, limits) are env-specific.

**Files (future).** `infra/monitoring/` — `prometheus.yml` (+ textfile/pushgateway), `alerts.yml`,
Grafana dashboard JSON, Loki + promtail config.

---

## 5. Item 10 — Helm chart

**Problem.** Deployment is raw, pricer-only K8s YAML; no parameterization.

**Design.** One chart `infra/helm/faultline/` templating the whole stack from `values.yaml`:
image registry + git-SHA tag, per-job schedules + resource requests/limits, the read-API
Deployment + Service + Ingress (with the gateway/TLS of §2), the data PVC, `PRICER_*` env +
Secret refs (API keys via K8s Secrets — the item-2 settings read them transparently), and the
monitoring sidecars. Replaces the per-module YAML drift; `helm install`/`upgrade` per environment
(`values.dev.yaml` / `values.prod.yaml`, mirroring the `PRICER_ENV` config overlay).

---

## 6. Item 10 — Backup & retention

- **Retention** is already coded ([common/store/retention.py](../../src/common/store/retention.py) +
  [gc.py](../../scripts/scenariogen/gc.py), item 8): schedule the GC as a weekly DAG step. Extend the
  same `prune_by_age` to the pricer result store + run-manifests if their growth warrants it
  (versions/sign-off stay reference-critical — never pruned).
- **Backup:** nightly snapshot of the `data/` volume (or object-store sync of
  `data/*/store/**` + `data/marketdata/snapshots/**`) with a retention window; verify a restore once.
  The content-addressed snapshots + run-manifests make a restored state self-describing.

---

## 7. Consolidated go-live checklist (env-specific values to supply)

- API key(s) / OIDC provider for the gateway; `PRICER_API_KEY` secret (§2).
- K8s Secrets for source API keys (FRED/Stooq/FINRA/EIA/OpenFIGI) and `MACRORAG_API_KEY` (item 2).
- Real risk limits (`pricer_config.json → risk.limits`) and scenario limits
  (`configs/scenariogen/limits.json`) — items 7/8 ship placeholders.
- EOD SLA time + per-source staleness thresholds (`configs/marketdata/dq_gate.json`, item 5).
- Alertmanager destinations (Slack webhook / email / PagerDuty) + thresholds (§4).
- Backup target (volume snapshot or object-store bucket) + retention window (§6).
- MacroRAG base URL + enable flag (`configs/scenariogen/macro.json`, item 8).
- TLS certificate + ingress hostname (§2).

---

## 8. Non-goals (for < 50 users)

HA / multi-region, a database server (the Parquet+DuckDB store stays), service mesh, autoscaling,
multi-tenant authz, a distributed tracing backend beyond the optional OTel export already present.
No pricing/risk math changes. This layer is purely operational.
