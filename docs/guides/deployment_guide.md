# Deployment Guide — containers, dual-mode storage, and AWS

How to run the whole stack in **containers** locally, the **`deploy_mode`** knobs that let the same
images run against a local filesystem **or S3**, and where the AWS story lives. This is the single
operator-facing deployment reference — the other guides link here rather than repeat it.

- Cloud architecture + the phased change list: **[AITrade_aws_deployment_design.md](../design/AITrade_aws_deployment_design.md)** (Phases A–D delivered; E infra-as-code + F CloudWatch need live AWS).
- Bare-metal (non-container) run order: [README.md](README.md#the-endtoend-run-order).

---

## 1. Dual-mode storage — one switch, local FS or S3

Every `data/` read and write in `src/` routes through the storage layer
([src/common/storage/](../../src/common/storage/)), selected by two env vars. **Defaults reproduce
the historical local behavior byte-for-byte** — with neither var set, nothing changes.

| Variable | Default | Meaning |
|---|---|---|
| `PRICER_DEPLOY_MODE` | `local` | `local` (filesystem) or `aws` (S3 + cloud job execution). |
| `PRICER_DATA_URI` | `<repo>/data` | Root of the data tree: a local path, or `s3://bucket/prefix`. In `aws` mode it is **required**. |

So `PRICER_DATA_URI=s3://my-bucket/qlp-data` (with `PRICER_DEPLOY_MODE=aws`) points the entire
pipeline — marketdata CSVs, Parquet stores, snapshots, JSON reports — at S3. S3 access uses the
ambient AWS credential chain (an IAM instance role in production); DuckDB reads `s3://` via its
`httpfs` extension. `fsspec`/`s3fs` are import-lazy, so a pure local run never needs them at runtime.

> **The BFF read layer** (`ui/bff`) reads a `QLP_ROOT`/local-`PRICER_DATA_URI` (EFS or S3-synced)
> mount in v1; native BFF-over-S3 DuckDB reads are a documented follow-on (design §7.6).

---

## 2. Run the stack in containers (local parity)

Three images, all built from the repo root:

| Image | Base | Contents |
|---|---|---|
| **qlp-backend** (~930 MB) | `python:3.12-slim` | QuantLib **PyPI wheel** + the pinned pip set + `src/` + `configs/`. **One multi-entrypoint image** for every runner *and* the pricer FastAPI service. |
| **qlp-bff** (~830 MB) | `python:3.12-slim` | FastAPI read layer. **No QuantLib** (its `src.pricer` imports are QuantLib-free). |
| **qlp-ui** (~76 MB) | `nginx-unprivileged:alpine` | The built Vite SPA, served static by **non-root** nginx on **:8080**, reverse-proxying `/api` → the BFF. The proxy target is `API_UPSTREAM` (default `bff:8088`), substituted into the nginx config at container start — no backend URL is baked into the JS. |

```bash
# From the repo root. GIT_SHA is baked into result provenance.
GIT_SHA=$(git rev-parse --short HEAD) docker compose up --build
#   UI:  http://localhost:8081     BFF: http://localhost:8088     pricer API: http://localhost:8080
```

Run a **batch job** in the backend image (shares the same mounted `./data` tree):

```bash
docker compose run --rm backend python -m src.pricer.runner
docker compose run --rm backend python -m src.pricer.risk_runner
docker compose run --rm backend python -m src.scenariogen.runner
```

[docker-compose.yml](../../docker-compose.yml) wires the three services over a shared `./data`
volume — the same file-based flow as bare metal — with per-service healthchecks (`backend` `/health`,
`bff` `/api/health`) gating start order. Dockerfiles: [infra/backend/](../../infra/backend/Dockerfile),
[infra/bff/](../../infra/bff/Dockerfile), [ui/app/Dockerfile](../../ui/app/Dockerfile).

Every env var is documented in **[.env.example](../../.env.example)** — `cp .env.example .env` and
compose reads it (secrets are placeholders; never commit real values). For AWS,
**[docker-compose.aws.yml](../../docker-compose.aws.yml)** is the EC2 stack: the same three images
**pulled from ECR** (`QLP_BACKEND_IMAGE` / `QLP_BFF_IMAGE` / `QLP_UI_IMAGE`, with the `build:` blocks
as a fallback), **secrets loaded via `env_file`** from a file rendered at instance boot (Secrets
Manager / SSM), the data tree on an **EFS mount**, `restart: unless-stopped`, and only the UI
published (`:8080`, the ALB target). See §6.

> **QuantLib is the PyPI wheel.** The local `py312` env installs QuantLib via pip (`pypi_0`) — that
> is the build the golden PVs are baselined against — so the backend image uses the same wheel (no
> conda). conda-forge `quantlib` is C++-library-only (no Python bindings). A trade priced in the
> container reproduces the local PV exactly.

---

## 3. Job execution — local subprocess vs cloud

The BFF's gated "run" writes go through a `deploy_mode`-selected `JobRunner`
([src/common/jobs/](../../src/common/jobs/)); job status records live in the storage layer, so both
tiers see them.

| Mode | Runner | How |
|---|---|---|
| `local` | `LocalSubprocessRunner` | Detached subprocess on this host (today's behavior). |
| `aws` | `AwsJobRunner` | Enqueue to **SQS** (`JOBS_QUEUE_URL`) for a backend worker to run the same container entrypoint. |

`JOBS_QUEUE_URL` — the SQS queue backend workers consume (AWS mode only). The queue + worker are
provisioned in Phase E (infra-as-code), not by this repo.

---

## 4. API authentication

The pricer FastAPI service ([src/pricer/service.py](../../src/pricer/service.py)) enforces an
`X-API-Key` header when `PRICER_API_KEY` is set (checked per-request, so key rotation needs no
restart); `/health` and `/metrics` stay open for liveness + Prometheus scrape. **When
`PRICER_API_KEY` is unset the check is a no-op** — local/dev use is unchanged. This is
defense-in-depth behind the gateway/ALB. The BFF's gated writes keep their own `QLP_WRITE_TOKEN`.

---

## 5. Deployment env-var reference

| Variable | Tier | Purpose |
|---|---|---|
| `PRICER_DEPLOY_MODE` | all | `local` \| `aws`. |
| `PRICER_DATA_URI` | all | Data-tree root (path or `s3://…`). |
| `PRICER_API_KEY` | pricer service | Enforce `X-API-Key` when set. |
| `JOBS_QUEUE_URL` | BFF (aws) | SQS queue for the `AwsJobRunner`. |
| `QLP_ROOT` | BFF | Repo/data root the BFF reads (mount in AWS v1). |
| `QLP_SERVICE_URL` | BFF | Upstream pricer service base URL. |
| `QLP_CORS_ORIGINS` | BFF | Comma-separated allowed CORS origins (ALB hostname in AWS); dev defaults otherwise. |
| `QLP_WRITE_TOKEN` | BFF | Locks the 4 gated writes. |
| `API_UPSTREAM` | UI | nginx `/api` reverse-proxy target (default `bff:8088`); set to the in-VPC BFF address in AWS. |
| `PRICER_ENV` | all | `dev`\|`uat`\|`prod` config overlay. |
| `PRICER_WORKERS` | pricer/risk | Parallelism default. |
| `PRICER_LOG_LEVEL` / `PRICER_JSON_LOGS` | all | Log level; JSON logs (recommended in prod). |
| `QLP_*_IMAGE` / `QLP_ENV_FILE` / `QLP_DATA_MOUNT` | AWS compose | ECR image URIs; the boot-rendered secrets env file; the EFS data mount. |

The full, sectioned template with placeholders is **[.env.example](../../.env.example)**. Feed-source +
LLM keys (`FRED/FINRA/EIA/OPENFIGI/OPENAI/ARAG/MACRORAG_*`) are in the
[operator handbook env table](README.md#environment-variables--secrets).

---

## 6. AWS

The full cloud topology (VPC/subnets/SGs, S3 + Gateway Endpoint, ECR, Secrets Manager, ALB+ACM, IAM
roles, the EC2 tiers, and the SQS queue + worker for `AwsJobRunner`), the service inventory, and the
phased change list are in **[AITrade_aws_deployment_design.md](../design/AITrade_aws_deployment_design.md)**.
Phases A–D (dual-mode storage, JobRunner, API auth, containers) are delivered; Phases E (Terraform)
and F (CloudWatch) require a live AWS account.

**Build → gate → push (the app side).** [`scripts/build_push_images.py`](../../scripts/build_push_images.py)
builds the three images for a target platform (default `linux/arm64`), runs the golden-PV gate **inside**
the built backend as the acceptance, and pushes to the `<registry>/genai-dev/qlp-*` ECR repos:

```bash
python scripts/build_push_images.py --dry-run                              # print the plan
AWS_PROFILE=genai-tf python scripts/build_push_images.py --tag arm64-<sha> # build -> gate -> push
```

The clean boundary: **this repo builds, verifies, and pushes its own artifacts**; the infra repo
(aws-genai-iac) owns the deploy — its `deploy_qlp_bundle.py` uploads the compose bundle + renders
`deploy.env`, and the EC2 bootstrap pulls the images. The two meet at the ECR registry, the deploy
bundle, and the env-var contract documented in [`.env.example`](../../.env.example).

---

## 7. Kubernetes batch path: `infra/pricer/k8s/`

[infra/pricer/](../../infra/pricer/) keeps a **pure-Kubernetes batch path** (weekday EOD CronJob +
ad-hoc Job + a `pricer-data` PVC) as an alternative to the EC2 + compose substrate — for teams that
already run a cluster. It now runs the **qlp-backend** image (`infra/backend/Dockerfile`); the old
conda-based Dockerfile here was **removed (2026-07-27)** — it installed conda-forge `quantlib`
(C++-only, no Python bindings) and never worked with the wheel the golden PVs are baselined against.
The manifests use `command:` (the full `python -m src.pricer.runner …`) because that image sets `CMD`,
not `ENTRYPOINT`. For S3-backed data, set `PRICER_DEPLOY_MODE=aws` + `PRICER_DATA_URI=s3://…` via a
ConfigMap/Secret instead of the PVC.
