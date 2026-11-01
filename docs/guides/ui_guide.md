# 🖥️ UI — Setup & Operator Guide

> A **Vite + React + TypeScript** explorer (`ui/app`) over a **FastAPI backend‑for‑frontend**
> (`ui/bff`). Primarily a **read** layer over everything the batch runners wrote, plus a small set
> of **gated, audited write actions** (risk‑limit edits, scenario sign‑off, run‑triggering, book
> activation) and a **live re‑price** proxy. Two lenses: **Business** (cockpit, valuations,
> scenarios, sizing) and **Technical** (LLM cost/ops, run traces, pipeline DAG, lineage, provenance).

Frontend: [ui/app/](../../ui/app/) · BFF: [ui/bff/](../../ui/bff/) · Docs: [ui/docs/](../../ui/docs/)

---

## 👔 For business users

Once running (http://localhost:5173), the UI gives you:

- **Cockpit / Control Room** — book valuation, governance posture, limits, drift at a glance.
- **Pricing** — book → asset class → trade, each with **calculation lineage** (inputs →
  intermediates → final, in plain English).
- **Risk** — VaR/ES with a histogram, sensitivity drill‑downs, P&L explain, backtest, limits.
- **Stress Scenarios** — scenario×tier grid, shock‑derivation (correlation heatmap), capital
  impact, plausibility, versions, and analyst sign‑off.
- **Market Data** — feed catalog with freshness, data‑quality verdicts, and which trades consume
  each feed.
- **Portfolio Construction** — risk-appetite position sizing: the four mandates (balanced /
  core / aggressive growth / risky, with **core** the active default) as comparison cards, a
  PV/Risk/Capital re-valuation per profile, the
  full per-trade sizing table, per-profile risk contributions, and an educative methodology tab.

The **Business ⇄ Technical** toggle (top bar) switches the whole app between the
decision‑oriented and the engineering‑oriented views.

---

## 🛠️ For technical operators

### Architecture & ports

```
Browser ──http──> ui/app (Vite dev server, :5173)
                      │  proxies /api  →
                      ▼
                  ui/bff (FastAPI, :8088) ──reads──> data/ (Parquet/DuckDB + JSON)
                      │
                      └─proxies /health, /metrics → pricer service (:8080, optional)
```

| Component | Port | Tech | Prereqs |
|---|---|---|---|
| Frontend `ui/app` | **5173** | Vite · React 18 · TS · React Router · TanStack Query · Tailwind · ECharts | Node v24.14.0 |
| BFF `ui/bff` | **8088** | FastAPI · uvicorn · DuckDB · pandas · pyarrow | conda `py312` |

### 1. Start the BFF

From the **repo root** (so the `ui` package resolves):

```powershell
$env:PYTHONPATH = $PWD.Path   # the repo root
conda run --no-capture-output -n py312 uvicorn ui.bff.app.main:app --port 8088 --reload
```

- OpenAPI docs: http://127.0.0.1:8088/docs
- Env config ([ui/bff/app/settings.py](../../ui/bff/app/settings.py)): `QLP_ROOT` (default = repo root,
  where `data/` is read from — point at an EFS/S3-synced mount in AWS), `QLP_SERVICE_URL` (default
  `http://127.0.0.1:8080`, the upstream pricer service for the health/metrics proxy), and
  `QLP_CORS_ORIGINS` (comma-separated allowed origins; defaults to the Vite dev origins).
- CORS allows the Vite dev origins by default (`localhost:5173`, etc.), overridable via
  `QLP_CORS_ORIGINS`. All routes are under **`/api`**.
- See [ui/bff/README.md](../../ui/bff/README.md).

### 2. Start the frontend

The UI uses Node v24.14.0; set `$node` to its install folder (or call `npm` directly if it is on PATH):

```powershell
$node = "<node-v24.14.0-install-dir>"
& "$node\npm.cmd" install        # first time only
& "$node\npm.cmd" run dev        # http://localhost:5173
```

npm scripts ([ui/app/package.json](../../ui/app/package.json)): `dev` (Vite), `build`
(`tsc -b && vite build`), `preview`, `typecheck`. The dev server proxies `/api` →
`http://127.0.0.1:8088` (override with `VITE_BFF_TARGET`); see [ui/app/vite.config.ts](../../ui/app/vite.config.ts).
See [ui/app/README.md](../../ui/app/README.md).

### 3. (Optional) Start the upstream pricer service

Only needed for the live `/api/system/health` and `/api/system/metrics` proxies; every other
endpoint reads stored data and works without it.

```powershell
conda run --no-capture-output -n py312 uvicorn src.pricer.service:app --port 8080
```

---

## 🔌 Endpoint map (all under `/api`)

This is a summary — the authoritative, always-current list is the OpenAPI at
`http://127.0.0.1:8088/docs`. Gated writes (POST) are marked 🔒.

| Area | Key routes | Backed by |
|---|---|---|
| System/Ops | `/system/runs`, `/system/runs/{id}`, `/system/pipeline`, `/system/modules`, `/system/config`, `/system/provenance`, `/system/health`, `/system/metrics`; `/write/mode`, `/roles`, `/audit`, `/jobs/catalog`, `/jobs` 🔒, `/jobs/{id}` | run manifests, span store, config, audit trail, jobs |
| Governance | `/governance/posture`, `/governance/model-inventory`, `/governance/report`, `/governance/drift` | run manifests, governance JSON |
| Pricing | `/pricing/dates`, `/pricing/book`, `/pricing/book/history`, `/pricing/trade/{id}`, `/pricing/trade/{id}/docs`, `/pricing/pv-history/{id}`, `/pricing/live/{id}`, `/lineage/{id}` | pricer Parquet store + per‑trade/lineage JSON + `docs/pricing/*` + live service |
| Risk | `/risk/dates`, `/risk/report`, `/risk/var-construction`, `/risk/limits` (GET), `/risk/limits` 🔒 | risk JSON; limit edit writes `configs/pricer/pricer_config.json` |
| Market Data | `/md/feeds`, `/md/overview`, `/md/dependency`, `/md/coverage`, `/md/dq`, `/md/feeds/{id}` + `/series` + `/rows` | marketdata CSVs + config + snapshot manifests + consumption map |
| Scenarios | `/scenarios`, `/scenarios/covariance`, `/scenarios/versions`, `/scenarios/signoff` 🔒, `/scenarios/macro/health`, `/scenarios/reverse`, `/scenarios/{name}` + `/results` `/narrative` `/attribution` `/trace` `/cost` | scenario report + reverse + trace JSONL + Parquet stores + LLM artifacts |
| LLM Cost & Ops | `/llmops/summary`, `/llmops/runs`, `/llmops/runs/{id}`, `/llmops/traces`, `/llmops/traces/{id}`, `/llmops/health` | `scenario_llm/*` manifests + dual‑RAG traces + LLM config |
| Portfolio Construction | `/sizing`, `/sizing/{profile}`, `/sizing/rebalance/{profile}`, `/sizing/{profile}/variant`, `/sizing/active`, `/sizing/activate` 🔒 | sizing report JSON; activate writes `src/pricer/portfolio/portfolio.json` |

**Gated writes (4, all token+RBAC guarded and audited):** `POST /risk/limits`,
`POST /scenarios/signoff`, `POST /jobs`, `POST /sizing/activate` — see the write-gate note below.

---

## ⛓️ Runner prerequisite chain — what lights up each page

The BFF reads what the runners wrote. Run these first (all from repo root, in `py312`):

| Run this | Lights up | Required? |
|---|---|---|
| `python -m src.pricer.runner` | Pricing pages, system runs, governance posture | **Yes — base layer** |
| `python -m src.pricer.lineage_runner` | `/lineage/{id}` (calc lineage on Pricing) | Recommended |
| `python -m src.pricer.risk_runner` | Risk pages | For Risk |
| `python -m src.scenariogen.runner` | Stress Scenario pages | For Scenarios |
| `python -m src.pricer.sizing.sizing_runner` | Portfolio Construction pages | For Portfolio Construction |
| `python -m src.pricer.governance_runner` | `/governance/report`, `/governance/drift` | Optional |
| `src.marketdata.*` runners | Market Data pages | For Market Data |

**Minimal viable demo:**

```powershell
$env:PYTHONPATH = $PWD.Path   # the repo root
python -m src.pricer.runner             # required
python -m src.pricer.lineage_runner     # recommended
python -m src.pricer.risk_runner        # for Risk page
python -m src.scenariogen.runner        # for Scenarios page
# then start the BFF (:8088) and the frontend (:5173)
```

---

## 🚀 Production notes

- Serve a real build: `npm run build` → static `dist/` behind a reverse proxy (not `npm run dev`).
  The **qlp-ui** container ([ui/app/Dockerfile](../../ui/app/Dockerfile)) does exactly this — nginx
  serving `dist/` and proxying `/api` → the BFF; see the [deployment guide](deployment_guide.md).
- Restrict CORS origins via the `QLP_CORS_ORIGINS` env var (comma-separated) to your domain.
- Set `QLP_ROOT` so the BFF can see the `data/` volume (an EFS/S3-synced mount in AWS); set
  `QLP_SERVICE_URL` to the real pricer service. If that service has `PRICER_API_KEY` set, the BFF's
  live re-price/health proxy needs the key too.
- **Write gate + RBAC** ([ui/bff/app/security.py](../../ui/bff/app/security.py)): the 4 write
  endpoints require an `X-QLP-Token` matching `QLP_WRITE_TOKEN` (when set; otherwise local‑trust),
  carry an `X-QLP-Actor`, and enforce per‑operator roles from [configs/ui/roles.json](../../configs/ui/roles.json)
  (`viewer < approver < operator < admin`); every write is appended to `data/pricer/audit/audit.jsonl`.
- **Read endpoints are unauthenticated.** Do not expose the BFF publicly without an auth layer in
  front of the read routes; set `QLP_WRITE_TOKEN` to lock down writes.

---

## 🚑 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Frontend loads but every page is empty | Runners haven't produced data | Run `src.pricer.runner` (+ risk/scenario/lineage) first. |
| `/api/...` requests 404 / connection refused | BFF not running or wrong port | Start the BFF on **8088**; confirm the Vite proxy target. |
| Lineage panel says "not available" | `lineage_runner` hasn't run | `python -m src.pricer.lineage_runner`. |
| `ModuleNotFoundError: ui` when starting BFF | Wrong cwd / `PYTHONPATH` | Start from repo root with `$env:PYTHONPATH` set. |
| `npm`/`node` not found | Node not on PATH | Use the full path to your Node install's `npm.cmd`. |
| `/api/system/health` shows "upstream down" | Pricer service not started | Optional — start it on :8080, or ignore; stored endpoints still work. |
| CORS error in browser console | Origin not whitelisted | Add your origin to `QLP_CORS_ORIGINS` (comma-separated) and restart the BFF. |
| Stale numbers after a new run | Browser/React‑Query cache | Hard‑refresh; data is keyed by valuation date. |
