# Faultline — BFF (Backend-for-Frontend)

A **FastAPI** service the React UI (`ui/app`) consumes. Primarily a **read layer** over the
platform's Parquet/DuckDB stores, config JSON, per-trade/lineage JSON, and provenance files —
plus a small set of **gated, audited write actions** and a **live re-price** proxy. It never
forks pricing logic: reads serve what the batch runners wrote; the one live path proxies the
upstream pricer service.

**Full endpoint map, ports, and run order:** [docs/guides/ui_guide.md](../../docs/guides/ui_guide.md).
The always-current API reference is the OpenAPI at `http://127.0.0.1:8088/docs`.

## Route groups (all under `/api`)

`system` (runs / pipeline DAG / modules / config / provenance / health+metrics proxy / jobs /
audit / roles / write-mode) · `governance` (posture / model-inventory / report / drift) ·
`pricing` (book / trade / pv-history / docs / **live re-price** / lineage) · `risk` (report /
var-construction / **limits** GET+POST) · `md` (feeds / overview / dependency / coverage / dq /
series / rows) · `scenarios` (catalog / detail / results / covariance / versions / **signoff** /
reverse / narrative / attribution / trace / cost / macro-health) · `llmops` (LLM cost/ops
summary / runs / traces / health) · `sizing` (report / rebalance / variant / active / **activate**).

**Gated writes (4, token + RBAC guarded and audited):** `POST /risk/limits`,
`POST /scenarios/signoff`, `POST /jobs` (whitelisted runner triggering — execution + status records
delegate to `src/common/jobs`: a local subprocess, or an SQS enqueue in `aws` mode),
`POST /sizing/activate`. The write gate is [app/security.py](app/security.py) (`X-QLP-Token` vs
`QLP_WRITE_TOKEN`, else local-trust; per-operator roles from `configs/ui/roles.json`); every write
appends to `data/pricer/audit/audit.jsonl`.

## Run

From the **repo root** (so the `ui` package resolves), using the platform's `py312` env:

```powershell
$env:PYTHONPATH = $PWD.Path   # the repo root
conda run --no-capture-output -n py312 uvicorn ui.bff.app.main:app --port 8088 --reload
```

- OpenAPI docs: <http://127.0.0.1:8088/docs>
- The Vite dev server proxies `/api` here (see `ui/app/vite.config.ts`).

## Config (env)

| Var | Default | Meaning |
|---|---|---|
| `QLP_ROOT` | repo root (3 levels above `app/`) | where to read `data/` from (a local `PRICER_DATA_URI` overrides; an EFS/S3-synced mount in AWS) |
| `QLP_SERVICE_URL` | `http://127.0.0.1:8080` | upstream pricer service base URL (health/metrics proxy + live re-price) |
| `QLP_WRITE_TOKEN` | unset (local-trust) | when set, required as `X-QLP-Token` on the 4 write endpoints |
| `QLP_CORS_ORIGINS` | dev origins (`localhost:5173`…) | comma-separated allowed CORS origins (set to the ALB hostname in AWS) |
| `PRICER_DEPLOY_MODE` / `JOBS_QUEUE_URL` | `local` / unset | job execution mode + the SQS queue for the `aws` `AwsJobRunner` — see the [deployment guide](../../docs/guides/deployment_guide.md) |
