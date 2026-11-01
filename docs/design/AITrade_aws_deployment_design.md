# AWS Cloud Deployment Design — Dual-Mode (Local FS + AWS/S3)

Author: Architecture review (Claude)
Date: 2026-07-16
Status: **Design / plan.** Change list is phased and local-safe — every step keeps the current
Windows-local deployment working unchanged (default `deploy_mode=local`).
Related: [AITrade_production_hardening_design.md](AITrade_production_hardening_design.md) (items 1–10),
[AITrade_deployment_runbook_design.md](AITrade_deployment_runbook_design.md) (K8s/monitoring blueprint —
this doc is the **AWS-specific** realization + the dual-mode code layer that runbook did not cover).

---

## 0. TL;DR

- The platform is **file-based, in-process, small-data** (whole `data/` tree = **36 MB**; full pricing
  run ≈ **1.2 s**; only full-reval VaR is CPU-heavy, and it is embarrassingly parallel and bursty).
- **There is no storage abstraction today** — ~109 direct `open()`/`pd.read_*`/`pyarrow`/`duckdb` calls
  across 51 files, all rooted at a hardcoded `<repo>/data/…`. The single biggest workstream is
  introducing a `deploy_mode`-driven storage façade so the same code reads/writes **local FS or S3**.
- **The RAG stack (ARag, MacroRAG, RagInference, Qdrant, Postgres) is NOT in this repo** — it lives in
  sibling repos and is reached only over the network. This repo holds *clients* whose endpoints are
  **already env-configurable** (`ARAG_BASE_URL`, `MACRORAG_BASE_URL`, `RAGINFER_URL`). That is a big head
  start: cross-service wiring is mostly a config exercise, not a code exercise.
- **EC2 topology recommendation:** one **backend/compute** instance for *all five subsystems* + the
  pricer API (justified below by the tiny data + sub-second/bursty compute); a **separate UI/edge**
  instance (agreeing with the different-SG rationale); a **separate RAG-tier** instance for RagInference
  (+ optionally ARag/MacroRAG); **Qdrant separate**; **S3** as the shared store replacing the PVC.
- **Additional AWS services you'll want** (not in the brief): **ECR, Secrets Manager, S3 Gateway
  Endpoint, ALB + ACM (TLS), IAM instance profiles, CloudWatch Logs/Alarms**, and optionally **ECS on
  EC2** (instead of hand-run docker-compose), **SQS + SSM Run Command / AWS Batch** for on-demand jobs,
  and **EFS** as a low-code alternative/complement to S3. Rationale in §7.

---

## 1. Current-state summary (what we're deploying)

| Dimension | Reality (measured) | Deployment implication |
|---|---|---|
| **Data** | 36 MB total; `data/marketdata` 22 MB (options chains dominate), `data/pricer` 14 MB (Parquet store 7.8 MB / 940 tiny parquet). All MB-scale. | Storage is a *correctness/sharing* problem, not a *capacity* one. S3 or EFS both trivially hold it. |
| **Compute** | Full pricing run ≈ 1.2 s; full-reval VaR ≈ 750 days × 38 trades ≈ 28.5k repricings → *minutes* sequential, less with `--workers` (`ProcessPoolExecutor`). No Monte Carlo, no ML/GPU, no torch/faiss. | One multi-vCPU box handles everything. VaR is the only reason to buy cores. Bursty → good fit for on-demand jobs. |
| **Pipeline** | `marketdata → pricer → risk/scenariogen/sizing/governance` are **in-process Python imports** chained through the shared `data/` tree — **no HTTP between them**. | Co-locating the backend subsystems is natural; splitting them would fragment the shared tree for no gain. |
| **Only cross-service network** | scenariogen → ARag (`:8001` REST) / MacroRAG (`:8000` REST) / MCP subprocesses / RagInference (`:8799`) / OpenAI. Endpoints already `${VAR:-default}`-driven. | The RAG tier is a separate deployment unit; wiring is config. |
| **Long-running services** | 2 FastAPI apps: pricer API (`:8080`, stateless, reads snapshot) and UI BFF (`:8088`, reads `data/` + **spawns runner subprocesses** + proxies to pricer API). | BFF's subprocess-spawning is the one thing that breaks cleanly in cloud → needs a job-execution abstraction (§5). |
| **Config** | `PRICER_*` env settings (pydantic-settings) + JSON under `configs/` + optional `pricer_config.<env>.json` overlay keyed by `PRICER_ENV` (dev/uat/prod). `${VAR:-default}` expansion used only for RAG service locations. | We extend, not replace: add `PRICER_DEPLOY_MODE` + a data-URI and reuse the existing env plumbing. |
| **Packaging** | a pre-existing conda-based pricer Dockerfile (K8s CronJob + 5Gi `ReadWriteOnce` PVC), manual CI. **No** compose/Helm/Terraform/UI image/S3/boto3. | *Correction found while shipping:* that conda Dockerfile never worked — conda-forge `quantlib` is C++-library-only, and the local `py312` env actually installs the **QuantLib PyPI wheel** (`pypi_0`). So the backend is a plain `python:3.12-slim` + wheel (no conda); BFF/UI stay pure-pip/static. |

**Process-global caveat (drives isolation):** `ql.Settings.evaluationDate` is process-global, so all
parallelism is **process-level** and *each concurrent valuation date needs its own process*. Horizontal
scaling across processes/containers/instances is otherwise unconstrained (stateless, idempotent,
provenance-stamped outputs).

---

## 2. Design principles

1. **Local stays the default and stays intact.** `PRICER_DEPLOY_MODE` defaults to `local`; with it
   unset the code path is byte-for-byte today's behavior. AWS is opt-in via env.
2. **One seam, many call sites.** Route the ~109 I/O sites through a small storage façade rather than
   sprinkling `if aws:` everywhere. The façade is the *only* place that knows local-vs-S3.
3. **Abstract behavior, not just paths.** Two things differ between modes: (a) *where* bytes live
   (path vs `s3://`), and (b) *how* out-of-process work is launched (subprocess vs cloud job). Both get
   a parameterized interface.
4. **Config over code for endpoints.** Service locations, buckets, secrets — all env/SSM-supplied. No
   host/bucket literals in code (the RAG clients already follow this; extend it to storage + BFF).
5. **Reproducibility spine is sacred.** `snapshot_id`, `RunProvenance`, git-SHA image tags, pinned
   QuantLib — unchanged. S3 object versioning *reinforces* it; content-addressing already makes a
   restored state self-describing.
6. **Keep images few and lean.** One shared backend image (python-slim + QuantLib wheel) reused by every
   subsystem entrypoint; a slim pip BFF image; a tiny static UI image.

---

## 3. Dual-mode storage abstraction (the core workstream)

### 3.1 What exists vs what's missing

- **Missing:** any `StorageBackend`, `fsspec`, `boto3`, `s3fs`, `httpfs`, or `deploy_mode`. Confirmed by
  grep — zero matches.
- **Present but repeated:** (a) project-root resolution `Path(__file__).parents[2|3]` duplicated in ~25
  modules; (b) an atomic-JSON-write idiom (`tmp` + `fsync` + `os.replace`) copy-pasted into ~7 modules;
  (c) a Parquet-store idiom (`pq.write_to_dataset` + DuckDB `read_parquet('<glob>')` + `any(dir.glob())`
  guard) copy-pasted into 6 store classes; (d) the marketdata CSV hub (`data_store.save_csv/load_csv/
  upsert_csv`) and the single pricing read path (`MarketDataSnapshot.read_csv`).
- **Only existing central root:** `ui/bff/app/settings.py` already honors a `QLP_ROOT` env for its data
  dir. This is the pattern to generalize.

### 3.2 The façade — `src/common/storage/`

Introduce one module, backed by **fsspec** (gives `LocalFileSystem` and `S3FileSystem` under a single
API — so `LocalStorage`/`S3Storage` come almost for free, and pandas/pyarrow/duckdb all speak fsspec/S3
URIs natively).

```
src/common/storage/
  __init__.py
  mode.py       # deploy_mode() -> "local"|"aws" ; from PRICER_DEPLOY_MODE
  root.py       # data_root() -> str URI ("file:///abs/data" or "s3://bucket/prefix")
                #   from PRICER_DATA_URI (default file://<repo>/data). Replaces the ~25 parents[n] roots.
  fs.py         # get_fs() -> fsspec.AbstractFileSystem (cached per-process)
                # helpers: join(), exists(), glob(), open(), read_text(), read_bytes()
  atomic.py     # write_text_atomic()/write_bytes_atomic():
                #   local -> tmp+fsync+os.replace ; s3 -> single PUT (atomic per object)
  parquet.py    # write_dataset(table, root_uri, partition_cols, basename)  (filesystem=get_fs())
                # duckdb_query(sql, src_uri): INSTALL/LOAD httpfs when uri is s3://, then read_parquet
```

Design notes:
- **`data_root()`** is the single high-fanout choke point. Every `project_root / "data" / …` becomes
  `storage.join(data_root(), module, …)`. Local mode yields today's exact paths (so golden PVs,
  snapshots, and provenance hashes are unchanged).
- **pandas**: `pd.read_csv`/`to_csv` accept fsspec URLs directly — the marketdata `data_store` and the
  `SnapshotReader._read_csv`/`FactorHistory._read` paths pass a URI + `storage_options`.
- **pyarrow**: `pq.write_to_dataset(..., filesystem=get_fs())` and `read_parquet` with `filesystem=`.
- **DuckDB**: when the glob is `s3://…`, run `INSTALL httpfs; LOAD httpfs;` and rely on the instance
  IAM role via the AWS credential chain (`CREATE SECRET … TYPE s3 PROVIDER credential_chain`). Local mode
  is untouched. Note the 940-tiny-parquet glob over S3 has per-object latency — acceptable at this size;
  mitigations in §7.6 if the store grows.
- **Atomicity:** S3 PUT of a whole object is atomic, so the content-addressed "write-if-absent" and
  latest-wins upserts keep their semantics; only the *mechanism* (no `os.replace`) changes.

### 3.3 Refactor targets (highest-leverage choke points, in priority order)

| # | Site | File(s) | Why it's the seam |
|---|---|---|---|
| 1 | Root resolution | `pricer/config.get_project_root`, `marketdata/common/config_loader.get_project_root`, per-runner `_ROOT`, `ui/bff/app/settings.data_dir` | ~25 callers collapse onto `data_root()` |
| 2 | Atomic write | `output/result_writer._atomic_write_text` + 6 clones (snapshot.py, report.py, versioning.py, trace/store.py, llm/store.py…) | one `atomic.write_text_atomic()` |
| 3 | Parquet stores | `output/result_store`, `common/obs/run_manifest`, `pricer/obs/run_manifest`, `scenariogen/{dashboard,versioning,signoff}`, `portfolio/versioning` | one `parquet.write_dataset` + `duckdb_query` |
| 4 | Marketdata CSV hub | `marketdata/common/data_store.py` (`save/load/upsert_csv`, `write_provenance`) | single write path for all downloaders |
| 5 | Pricing read path | `pricer/loader/snapshot.py` (`read_csv`, `write_manifest`), `risk/factor_history._read` | single read path for pricing inputs |
| 6 | Snapshot manifests | `common/snapshot.py` (`sha256_file`, `write_manifest`) | provenance spine — must work over both |
| 7 | Retention/GC | `common/store/retention.py` | S3 lifecycle can supersede, but keep parity |

Estimated effort: façade + choke points 1–3 cover the large majority of call sites; 4–7 finish it. This
is mechanical, test-guarded refactoring (the existing pytest suite + golden PVs are the acceptance test —
local outputs must be identical byte-for-byte after the refactor).

### 3.4 New dependencies

`fsspec`, `s3fs` (pulls `aiobotocore`/`botocore`), and `duckdb`'s bundled `httpfs`. All pip, all in the
BFF and backend images. Pin them like everything else.

---

## 4. Service & endpoint wiring (mostly already done)

- **RAG endpoints**: `ARAG_BASE_URL`, `MACRORAG_BASE_URL`, `RAGINFER_URL`, `ARAG_HOME`, `MACRORAG_HOME`,
  `MCP_PYTHON`, `ARAG_API_KEY`, `MACRORAG_API_KEY`, `OPENAI_API_KEY` — **already** `${VAR:-default}`.
  AWS = supply private DNS / service-discovery names + keys from Secrets Manager. **No code change.**
- **BFF → pricer API**: `QLP_SERVICE_URL` (default `http://127.0.0.1:8080`) → backend private DNS.
- **BFF data root**: `QLP_ROOT` → align with `PRICER_DATA_URI` (BFF gets the same storage façade).
- **CORS**: `ui/bff/app/settings.py` hardcodes localhost dev origins — make the allowed origins env-driven
  so the ALB hostname is accepted in AWS.
- **API auth**: adopt the already-designed `PRICER_API_KEY` `X-API-Key` dependency on the pricer API and
  the BFF's existing `QLP_WRITE_TOKEN` gate; TLS terminates at the ALB (§7.4). No-op when unset → local
  parity preserved.

---

## 5. Job-execution abstraction (the one behavioral fork)

**Problem.** `ui/bff/app/jobs.py` launches whitelisted runners as detached
`subprocess.Popen([sys.executable, ...])` on the *same host* and tracks JSON status under
`data/pricer/jobs/`. In AWS the BFF lives on the UI/edge instance and must **not** hold QuantLib or run
compute; the heavy runners belong on the backend tier.

**Design — a `JobRunner` interface selected by `deploy_mode`:**

```
src/common/jobs/runner.py
  class JobRunner(Protocol): def submit(job_key, argv) -> JobHandle; def status(id) -> JobStatus
  LocalSubprocessRunner   # today's behavior (deploy_mode=local) — UNCHANGED
  AwsJobRunner            # deploy_mode=aws
```

`AwsJobRunner` options (pick per §7 topology):
- **SSM Run Command** to the backend instance (simplest; no new infra; runs the same container entrypoint).
- **SQS → backend worker** that pulls job messages and runs the container entrypoint (decouples UI from
  backend; survives backend restarts).
- **ECS RunTask / AWS Batch** (cleanest isolation; one task per job; auto-captures logs to CloudWatch).

Job *status* moves from local JSON files to the shared store (S3) or a small DynamoDB table so both tiers
see it. The BFF's whitelist and RBAC/audit stay as-is.

---

## 6. Containerization plan

Goal: **few images, lean images, UI containerized, deps parameterized.**

| Image | Base | Contents | Approx size | Notes |
|---|---|---|---|---|
| **`qlp-backend`** | `python:3.12-slim` | QuantLib 1.42.1 **PyPI wheel** + pip runtime deps + `src/` + `configs/`. Multi-entrypoint. | ~830 MB (measured) | **One image for all subsystems** — marketdata, pricer, risk, scenariogen, sizing, governance, and the pricer FastAPI API. Entry chosen by command (`python -m src.pricer.runner`, `…risk_runner`, `uvicorn src.pricer.service:app`, …). **No conda:** the local py312 env installs QuantLib via pip (`pypi_0`) — that wheel is what the golden PVs are baselined against, and conda-forge `quantlib` is C++-library-only (no Python bindings). Using the wheel is both reproducible *and* far smaller than a conda image. |
| **`qlp-bff`** | `python:3.12-slim` | FastAPI + uvicorn + duckdb + pandas + pyarrow + fsspec/s3fs. **No QuantLib** (jobs offloaded via §5). | ~350–450 MB | Reads the store; proxies to the pricer API; submits jobs via `AwsJobRunner`. |
| **`qlp-ui`** | `nginx-unprivileged:alpine` | Built Vite SPA (`ui/app` → `dist/`), served **non-root on :8080**; an nginx *template* proxies `/api` → the BFF via a runtime `API_UPSTREAM` (no backend URL baked into the JS). | ~50–70 MB | **Satisfies "UI must be containerized."** Static; no Node at runtime (multi-stage `node:24.14.0` build → copy `dist`). |

**Out of scope of this repo's images:** RagInference, ARag, MacroRAG, Qdrant, Postgres — each ships from
its own repo. The brief's image-size concern ("ragInference, MacroRag, ARag are dependencies") is
addressed by keeping *those as separate images/instances* and never bundling them into the Faultline
images. This repo only carries the thin RAG **clients** (already in the ~1 GB backend image).

**Delivered (2026-07-27):** `.dockerignore` (excludes `data/ .git logs/ tests/` and now `.env*`); a root
[`docker-compose.yml`](../../docker-compose.yml) wiring `qlp-backend` + `qlp-bff` + `qlp-ui` with
per-service healthchecks (`/health`, `/api/health`) gating start order, for **local container parity**
(optional — bare-metal local still works); the [`docker-compose.aws.yml`](../../docker-compose.aws.yml)
EC2 stack (ECR image refs `QLP_*_IMAGE` + build fallback, `env_file` secrets rendered at boot, EFS data
mount, `restart: unless-stopped`, only the UI published as the ALB target); and a committed
[`.env.example`](../../.env.example) documenting every `PRICER_*` / `QLP_*` / `API_UPSTREAM` variable as
a placeholder (S3 credentials come from the instance role, never the file). The legacy conda
`infra/pricer/Dockerfile` was **removed**; `infra/pricer/k8s/` now runs the `qlp-backend` image.

**Build → gate → push (2026-07-29):** [`scripts/build_push_images.py`](../../scripts/build_push_images.py)
is the **app-side** half of the deploy — it builds the three images for a target platform (default
`linux/arm64`), runs the golden-PV gate **inside** the built backend as the acceptance, and pushes to the
`<registry>/genai-dev/qlp-*` ECR repos. The **infra** side (the sibling `aws-genai-iac` repo) owns the
rest: `deploy_qlp_bundle.py` uploads the compose bundle + renders `deploy.env`, and the EC2 bootstrap
pulls the images. The two repos meet at exactly three seams — the **ECR registry**, the **deploy bundle**,
and the **env-var contract** in [`.env.example`](../../.env.example) (which the infra `deploy.env` tracks).

---

## 7. AWS target architecture

### 7.1 Tier & instance topology (the judgement calls you asked for)

```
                         Internet
                            │ 443 (TLS/ACM)
                    ┌───────▼────────┐
                    │      ALB       │  + WAF (optional)
                    └───────┬────────┘
        ┌──────────── Public subnet ────────────┐
        │        UI / EDGE instance (SG-ui)      │   ← separate instance (your call: agreed)
        │   qlp-ui (nginx SPA) + qlp-bff (:8088) │
        └───────┬───────────────────────┬────────┘
        reads   │ S3 (VPC endpoint)      │ :8080 to backend (SG-backend)
                ▼                        ▼
   ┌─────── Private subnet ────────────────────────────────────────────┐
   │  BACKEND / COMPUTE instance (SG-backend)                           │
   │   qlp-backend: pricer API (:8080) + all runners (jobs via §5)      │   ← ONE instance for all 5 subsystems
   │        │ reads/writes S3 (VPC endpoint)                            │
   │        │ scenariogen → RAG tier (REST/MCP) ──────────────┐         │
   │  ┌─────▼─────────────────────────────────────────┐       │         │
   │  │  RAG tier instance(s) (SG-rag)                 │       │         │
   │  │   RagInference (:8799)  [+ ARag :8001,         │◄──────┘         │
   │  │    MacroRAG :8000  — sibling repos]            │                 │
   │  └───────┬───────────────────────┬───────────────┘                 │
   │          │ Qdrant (:6333)         │ Postgres                        │
   │  ┌───────▼──────────┐    ┌────────▼─────────┐                       │
   │  │ Qdrant instance  │    │ RDS Postgres     │  (ARag backend)       │
   │  │  (SG-qdrant)     │    │  (SG-db)         │                       │
   │  └──────────────────┘    └──────────────────┘                       │
   └────────────────────────────────────────────────────────────────────┘
     S3 (data store) · ECR (images) · Secrets Manager · CloudWatch · IAM roles
```

**Decision 1 — All five subsystems on ONE backend EC2 instance. Recommended.**
Justification from the measured profile: the whole `data/` tree is 36 MB, the pipeline is in-process
Python chained through that shared tree, a full pricing run is ~1.2 s, and the *only* heavy job
(full-reval VaR / ES-under-shock) is embarrassingly parallel within one box and bursty/on-demand.
Splitting subsystems onto separate instances would (a) fragment the shared data tree over the network for
no throughput gain, and (b) add orchestration for a workload a single 4–8 vCPU box finishes in minutes.
Size: **compute-optimized `c7i.xlarge` (4 vCPU / 8 GB)** baseline; step to `c7i.2xlarge` (8 vCPU) if VaR
wall-clock matters. RAM is a non-constraint (data is MB-scale). *With S3 backing, a future split (e.g. a
dedicated VaR worker fleet) becomes a zero-code-change option — but it is not needed for v1.*

**Decision 2 — UI/edge on a SEPARATE instance. Recommended (agreeing with you).**
The UI/BFF tier has a genuinely different security posture: it's the only public-facing tier (behind the
ALB), so it gets its own SG (ingress 443 from ALB only; egress to backend `:8080`, S3, Secrets Manager).
The backend SG stays fully private (no internet ingress; egress to RAG tier + S3 + market-data APIs +
OpenAI). Keeping them separate means a UI compromise can't directly reach the compute/data plane except
through the two narrow, authenticated channels (pricer API + read-only S3). This also lets the BFF image
stay QuantLib-free (§5/§6).

**Decision 3 — RagInference on a SEPARATE instance in the same VPC. Recommended (as you specified).**
It has a different resource profile (embeddings + reranker; potentially GPU/high-CPU) and lifecycle
(model files, its own scaling) and belongs to sibling repos. Its own SG (ingress from backend + ARag +
MacroRAG only). ARag and MacroRAG can co-locate on this RAG tier or take their own instance — that choice
belongs to those repos; Faultline only needs their URLs.

**Decision 4 — Qdrant separate, in VPC. As you specified.**
Self-host Qdrant on its own small instance (or Qdrant Cloud BYOC via PrivateLink). Private SG (ingress
from the RAG tier only). ARag's Postgres → **RDS** (managed) or on-instance; RDS recommended for backups.

### 7.2 Runtime substrate: EC2 + compose (v1) → ECS (scale-up)

For <50 users and this scale, run the containers on the EC2 instances directly with **docker-compose**
(matches your EC2 framing, minimal moving parts). Evolution path: **ECS on EC2** (or Fargate for the BFF/
UI) once you want managed task lifecycle, rolling deploys, and native CloudWatch log routing. The images
and env contract are identical either way, so this choice is deferrable.

### 7.3 Storage: S3 (primary) — with EFS as a documented alternative

- **S3 `qlp-data` bucket** replaces the 5Gi PVC. Prefix layout mirrors `data/` (`marketdata/`, `pricer/`,
  `risk/`, `scenariogen/`). Enable **versioning** (reinforces the reproducibility spine) and a
  **lifecycle policy** for retention (can supersede `retention.py` GC for the prunable stores; keep
  versions/sign-off forever). Access strictly via **VPC Gateway Endpoint** (free, keeps S3 traffic off
  the internet) + IAM instance-profile policies (backend: RW; BFF: RO on read prefixes, RW only on the 4
  gated-write prefixes).
- **EFS alternative (call-out):** because every component just wants a shared POSIX tree, mounting **EFS**
  at `/app/data` and pointing `PRICER_DATA_URI=file:///app/data` gives dual-mode with *near-zero code
  change* (no storage façade needed for the FS case) and no DuckDB-over-S3 latency. Trade-off: EFS is
  pricier per-GB, NFS semantics, and doesn't give S3's object-versioning/lifecycle. **Recommendation:**
  build the S3 façade (per the brief and for clean object semantics/versioning), and keep EFS in your back
  pocket for (a) a fast first cloud bring-up and (b) any POSIX-only scratch (DuckDB spill, conda temp).

### 7.4 Networking & security

- **VPC** with public subnet (ALB only) + private subnets (backend, RAG, Qdrant, RDS). NAT Gateway for
  backend egress to market-data APIs + OpenAI (on-demand ingestion + LLM).
- **Security groups** per tier as in the diagram — least-privilege, referencing SGs not CIDRs.
- **ALB** terminates TLS via **ACM**; enforces auth at the edge (static API key for <50 users, or OIDC/
  corp-SSO). **WAF** optional. In-app `PRICER_API_KEY` / `QLP_WRITE_TOKEN` as defense-in-depth.
- **Secrets Manager** (or SSM Parameter Store SecureString) holds every key the settings already read
  from env: `PRICER_FRED_API_KEY`/`FRED_API_KEY`, `STOOQ_API_KEY`, `FINRA_API_KEY(_SECRET)`, `EIA_API_KEY`,
  `OPENFIGI_API_KEY`, `OPENAI_API_KEY`, `ARAG_API_KEY`, `MACRORAG_API_KEY`, `QLP_WRITE_TOKEN`,
  `PRICER_API_KEY`. Injected as env at container start → **no settings code changes** (pydantic-settings
  already sources env).
- **IAM instance profiles** give S3 + Secrets access without static credentials; DuckDB `httpfs` and s3fs
  both use the instance-role credential chain automatically.

### 7.5 Ingestion & scheduling (kept CLI/UI-driven, as you specified)

Market-data ingestion stays **on-demand** — triggered from the CLI on the backend or from the UI (BFF →
`AwsJobRunner`). No scheduled scraping. *Optionally*, the EOD pricing/risk batch (already a K8s CronJob
today) can be scheduled with **EventBridge Scheduler → SSM Run Command / ECS RunTask** — but this stays
opt-in and separate from ingestion, honoring your "on-demand" requirement. The post-ingest **DQ gate**
(`scripts/marketdata/dq_gate.py`, exit-coded) runs as a job step exactly as today.

### 7.6 Observability

The code already emits Prometheus metrics + structured JSON logs + run-manifests (see the runbook doc's
metric inventory). On AWS:
- **CloudWatch Logs** for the JSON logs (container stdout → log groups, keyed by `run_id`/`snapshot_id`).
- **CloudWatch Alarms** on the batch-job metrics (EOD-did-not-run, job-failed, DQ-block, limit-breach) —
  mirror the Alertmanager rules from the runbook doc.
- Managed Prometheus/Grafana (**AMP/AMG**) if you want to reuse the existing Prometheus metric names and
  the dashboards sketched in the runbook doc, instead of re-expressing them as CloudWatch metrics.
- **S3-over-S3 latency note:** if the 940-tiny-parquet DuckDB glob gets slow from the BFF, mitigate by
  (a) a small local read-through cache on the edge instance, (b) EFS for the hot read stores, or (c)
  compacting the Parquet store (fewer, larger files) — all deferrable; data is tiny today.

---

## 8. AWS service inventory (for the infra component)

Priority key: **P0** = required to stand up v1 · **P1** = operationalize · **P2** = scale-up /
alternative · **P3** = nice-to-have. "In brief?" flags services the original brief named vs. ones added
by this review.

| # | Service | Category | Purpose in this deployment | Tier / component served | Config & dependency notes | In brief? | Priority |
|---|---|---|---|---|---|---|---|
| 1 | **S3** | Storage | Shared data store; replaces the 5Gi PVC. Versioning + lifecycle for retention | All tiers (backend RW; BFF RO + gated-write prefixes) | `PRICER_DATA_URI=s3://qlp-data/<env>`; via VPC endpoint + IAM role | Yes (implied) | **P0** |
| 2 | **ECR** | Registry | Private registry for the 3 images; git-SHA tags matching result provenance | Build/deploy pipeline | Pin to SHA tags, not `latest` | No | **P0** |
| 3 | **Secrets Manager** (or SSM Parameter Store) | Security | Holds all API keys; injected as env at container start — no settings code change | Backend (feed/OpenAI keys); UI/BFF (write token) | `FRED/FINRA/EIA/OPENFIGI/OPENAI/ARAG/MACRORAG_API_KEY`, `QLP_WRITE_TOKEN`, `PRICER_API_KEY` | No | **P0** |
| 4 | **VPC S3 Gateway Endpoint** | Networking | Keeps backend↔S3 traffic off the public internet (free) | Backend ↔ S3 | Route-table association only | No | **P0** |
| 5 | **IAM instance profiles** | Security | S3 + Secrets access without static creds; used by DuckDB `httpfs` & s3fs credential chain | Every EC2 tier (least-privilege per tier) | Backend RW S3; BFF RO + gated-write prefixes | No | **P0** |
| 6 | **ALB + ACM** | Networking | TLS termination + edge auth (API key or OIDC/SSO) for UI/BFF & pricer API | UI/edge tier (public 443) | ACM cert; in-app `PRICER_API_KEY`/`QLP_WRITE_TOKEN` as defense-in-depth | No | **P0** |
| 7 | **EC2** | Compute | Backend (all 5 subsystems + pricer API), UI/edge, RAG-tier, Qdrant | 4 tiers per §7.1 | Backend `c7i.xlarge`→`2xlarge`; containers via compose | Yes | **P0** |
| 8 | **CloudWatch Logs + Alarms** | Observability | JSON logs → log groups; alarms mirror the runbook's Alertmanager rules | All tiers | Keyed by `run_id`/`snapshot_id`; alarms on job-failed/DQ-block/limit-breach | No | **P1** |
| 9 | **SQS** | Integration | Decouples BFF job-submit from backend job execution (the `AwsJobRunner` path) | BFF → backend | Recommended job mechanism (survives restarts, native retries) | No | **P1** |
| 10 | **SSM Run Command / AWS Batch / ECS RunTask** | Compute / Jobs | On-demand execution of heavy runners (VaR, scenario) off the BFF host | Backend | Pick one to back `AwsJobRunner`; runs the same container entrypoint | No | **P1** |
| 11 | **RDS (PostgreSQL)** | Database | ARag's backend store (managed, backups) | RAG tier | Owned jointly with the ARag sibling repo | No | **P1** |
| 12 | **EventBridge Scheduler** | Orchestration | *Optional* EOD pricing/risk batch trigger (NOT ingestion — that stays on-demand) | Backend | Opt-in; → SSM Run Command / ECS RunTask | No | **P2** |
| 13 | **EFS** | Storage | Low-code shared-FS alternative to S3 / POSIX scratch (DuckDB spill, conda temp) | Backend / BFF | `PRICER_DATA_URI=file:///app/data` — near-zero code change | No | **P2** |
| 14 | **ECS (on EC2 / Fargate)** | Compute | Managed task lifecycle + rolling deploys (scale-up from raw compose) | All container tiers | Same image/env contract as compose; deferrable | No | **P2** |
| 15 | **CloudFront** | CDN | CDN for the SPA if later hosted statically on S3 instead of the nginx instance | UI/edge | Only if UI moves to S3 static hosting | No | **P3** |
| 16 | **WAF** | Security | Edge filtering in front of the ALB | UI/edge | Optional hardening | No | **P3** |

---

## 9. The change list (phased, local-safe)

Every phase leaves `deploy_mode=local` working. Gate after each: `ruff` + `mypy` + `pytest` (golden PVs
are the regression guard — local byte-for-byte parity is the acceptance test).

> **Status (2026-07-16): Phase A DELIVERED on `main`.** The `src/` layer is fully dual-mode — every read/write in marketdata,
> pricer, risk, scenariogen, sizing, and governance routes through `src/common/storage/`
> (`PRICER_DEPLOY_MODE` + `PRICER_DATA_URI`). Local output is byte-for-byte unchanged (ruff + mypy
> + full pytest incl. golden PVs green; a live default `pricer.runner` was smoke-tested). The S3
> backend is code-complete and unit-tested via fsspec `memory://` + S3-glob construction; live-AWS
> is the remaining acceptance test. **Documented follow-ons:** the BFF's ~15 DuckDB read modules
> read a `QLP_ROOT`/local-`PRICER_DATA_URI` (EFS / S3-synced) mount in v1 — native BFF-over-S3 is a
> follow-on (§7.6); `retention.py` stays local-FS (S3 lifecycle supersedes it); `authoring.py` and
> the sizing book-variant regeneration keep writing to the baked source-config tree by design.

### Phase A — Storage façade + `deploy_mode` (code; the big one)
1. Add `src/common/storage/` (`mode.py`, `root.py`, `fs.py`, `atomic.py`, `parquet.py`) — §3.2.
2. Add settings: `PRICER_DEPLOY_MODE` (`local`|`aws`, default `local`) and `PRICER_DATA_URI`
   (default `file://<repo>/data`) to `src/common/config/settings.py` (`PlatformSettings`). BFF: make
   `QLP_ROOT` resolve through the same façade; make CORS origins env-driven.
3. Refactor choke points 1–3 (root resolution, atomic writes, Parquet stores) onto the façade.
4. Refactor choke points 4–6 (marketdata CSV hub, pricing read path, snapshot manifests).
5. DuckDB `httpfs` path + credential-chain secret in `parquet.duckdb_query` (S3 branch only).
6. Add deps: `fsspec`, `s3fs` (pin). New unit tests: façade round-trips against a local temp dir and a
   moto/minio S3 fake; assert local paths unchanged.

> **Status (2026-07-16): Phase B DELIVERED on `main`.** `src/common/jobs/`
> adds a storage-backed `JobRecordStore` (records visible across tiers) + a `JobRunner` Protocol
> with `LocalSubprocessRunner` (today's behavior) and `AwsJobRunner` (SQS enqueue via lazy boto3,
> `JOBS_QUEUE_URL`); `get_job_runner()` selects by mode. The BFF `jobs.py` keeps its whitelist/gate/
> API and delegates. ruff + mypy + `tests/bff` green; local behavior unchanged.

### Phase B — Job-execution abstraction
7. `src/common/jobs/runner.py` with `JobRunner` Protocol + `LocalSubprocessRunner` (extract today's
   `jobs.py` logic verbatim) + `AwsJobRunner` (SSM/SQS/Batch — pick one). Route `ui/bff/app/jobs.py`
   through it by `deploy_mode`. Move job-status records into the store (S3/DynamoDB) so both tiers see them.

> **Status (2026-07-16): Phases C + D DELIVERED on `main`.** Three
> images build + run + verified (backend prices TRADE_22 to 689,772.14 in-container; full
> `docker compose` stack: UI→nginx→BFF `/api/health` 200, backend `/health` 200). Backend is
> `python:3.12-slim` + the QuantLib **PyPI wheel** (no conda). Phase D added the `PRICER_API_KEY`
> `X-API-Key` middleware on the pricer service (no-op when unset). **Remaining phases (E infra-as-
> code, F CloudWatch) need live AWS.**

### Phase C — Containerization
8. `infra/backend/Dockerfile` — `python:3.12-slim` + the QuantLib PyPI wheel, one image,
   multi-entrypoint (supersedes the broken conda `infra/pricer/Dockerfile`).
9. `infra/bff/Dockerfile` — python:3.12-slim, pip, no QuantLib.
10. `ui/app/Dockerfile` — multi-stage `node:24` build → `nginx:alpine` static serve + `/api` proxy.
11. `docker-compose.yml` (local container parity) + `docker-compose.aws.yml` overlay (env from
    Secrets/SSM, `PRICER_DEPLOY_MODE=aws`, `PRICER_DATA_URI=s3://…`).

### Phase D — API hardening
12. `PRICER_API_KEY` `X-API-Key` dependency on the pricer API (already designed in the runbook doc; no-op
    when unset). Confirm BFF `QLP_WRITE_TOKEN` + RBAC path in cloud.

### Phase E — AWS infra-as-code
13. `infra/aws/` — Terraform (or CDK): VPC + subnets + SGs, S3 bucket (versioning/lifecycle) + Gateway
    Endpoint, ECR repos, Secrets, IAM roles/instance profiles, ALB+ACM, the 4 EC2 tiers (user-data runs
    compose), CloudWatch log groups + alarms. RAG-tier/Qdrant/RDS as referenced modules (owned jointly
    with the sibling repos).
14. `docs/guides/aws_deployment.md` — runbook: build/push images, `terraform apply`, seed the S3 store
    (one-time `aws s3 sync data/ s3://…` or a first ingestion run), env matrix, smoke test.

### Phase F — Observability wiring
15. CloudWatch alarms mirroring the runbook doc's Alertmanager rules; optional AMP/AMG for the existing
    Prometheus metrics.

**Dependency order:** A → (B, C in parallel) → D → E → F. Phase A unblocks everything (it's the seam);
B and C are independent; E consumes B/C/D; F is last.

---

## 10. Environment-variable matrix (local vs AWS)

| Var | Local (default) | AWS |
|---|---|---|
| `PRICER_DEPLOY_MODE` | `local` | `aws` |
| `PRICER_DATA_URI` | `<repo>/data` (plain path) | `s3://qlp-data/<env>` |
| `PRICER_ENV` | `dev` | `prod` (drives config overlay) |
| `PRICER_WORKERS` | `1` | `4`–`8` (VaR fan-out = backend vCPUs) |
| `JOBS_QUEUE_URL` | unset (local subprocess) | SQS queue URL (backend worker consumes) |
| `QLP_ROOT` / BFF data | repo root | EFS / S3-synced mount (native S3 read = §7.6 follow-on) |
| `QLP_SERVICE_URL` | `http://127.0.0.1:8080` | backend private DNS `:8080` |
| `QLP_CORS_ORIGINS` | localhost:5173/4173 | ALB hostname |
| `ARAG_BASE_URL` / `MACRORAG_BASE_URL` / `RAGINFER_URL` | localhost:8001/8000/8799 | RAG-tier private DNS |
| `*_API_KEY`, `OPENAI_API_KEY`, `QLP_WRITE_TOKEN`, `PRICER_API_KEY` | `.env` / unset | Secrets Manager → env |

---

## 11. Risks & open decisions

- **DuckDB-over-S3 latency** for the many-small-parquet glob — fine at 36 MB; watch as the store grows;
  mitigations in §7.6. *Decision:* accept for v1.
- **BFF job-execution mechanism** (SSM vs SQS vs Batch) — pick one in Phase B. *Recommendation:* SQS →
  backend worker (decoupled, survives restarts, native retries).
- **EFS vs S3** — S3 per the brief; EFS documented as the low-code fast-start / POSIX-scratch option.
- **RAG tier ownership** — ARag/MacroRAG/RagInference/Qdrant/Postgres deploy from sibling repos; this
  plan only pins their *contract* (URLs + keys). Coordinate SGs and the VPC with those repos.
- **ECS vs compose** — start with EC2+compose (matches the brief), promote to ECS when managed lifecycle
  is wanted. Env/image contract is identical, so deferrable.
- **QuantLib image size** — the backend is `python:3.12-slim` + the QuantLib PyPI wheel (~930 MB
  measured; `--no-cache-dir` pip, no conda). One shared image (not per-subsystem) keeps total registry
  size down. The RAG images are separate repos and don't inflate this repo's footprint.
- **Golden-PV parity** — the storage refactor must not change any local bytes; the golden PV suite +
  `snapshot_id` stability is the hard gate on Phase A.
```
