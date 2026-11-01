# CLAUDE.md — `ui/`

`ui/app` (Vite + React 18 + TS frontend) over `ui/bff` (FastAPI backend-for-frontend). Primarily a
**read layer** over gitignored pipeline outputs under `data/`, plus 4 gated/audited writes and a
live re-price. Operator guide: [../docs/guides/ui_guide.md](../docs/guides/ui_guide.md). Design +
(historical) build plan: [ui/docs/](docs/).

## Run

```powershell
# BFF (FastAPI, :8088) — from repo root so the `ui` package resolves
$env:PYTHONPATH = $PWD.Path
conda run --no-capture-output -n py312 uvicorn ui.bff.app.main:app --port 8088 --reload   # OpenAPI at /docs

# Frontend (Vite, :5173) — proxies /api → :8088 (override VITE_BFF_TARGET)
$node = "<folder holding Node v24.14.0>"
& "$node\npm.cmd" install ; & "$node\npm.cmd" run dev
```

npm scripts: `dev`, `build` (`tsc -b && vite build`), `preview`, `typecheck`, `test` (vitest).
BFF env: `QLP_ROOT` (data dir, default repo root; an EFS/S3-synced mount in AWS), `QLP_SERVICE_URL`
(upstream pricer `:8080`, optional — powers only `/system/health` + `/metrics` + live re-price),
`QLP_WRITE_TOKEN` (locks writes), `QLP_CORS_ORIGINS` (comma-separated allowed origins; default = dev
origins). Deploy modes: [../docs/guides/deployment_guide.md](../docs/guides/deployment_guide.md).

## Frontend (`ui/app`)

- Stack: React 18 · Router 6 · TanStack Query 5 · Tailwind 3 · ECharts 5 (SVG). **No React Flow /
  shadcn.** The BFF API client is **hand-typed** in `src/lib/api.ts` (not generated).
- 13 feature folders under `src/features/`: cockpit, pricing, risk, marketdata, **signals** (P6:
  B6 option-implied surfaces + A5 News & Events), scenarios, sizing, llmops, governance, ops, system,
  lineage, admin. Business ⇄ Technical lens toggle.
- Scenarios tabs include **Layer Ablation** (`/scenarios/ablation`, `AblationWaterfall.tsx`) and **Signal
  Health** (`/scenarios/signal-health`); scenario detail gains a **Layers** tab and a not-run view
  (`ScenarioDefinition.tsx`: outcome, gate findings, definition, replay window). `SignalHealthBadge`
  sits on the Cockpit and Control Room. Chart math lives in `features/scenarios/waterfall.ts` (vitest).
  The **Measurement** tab (`/scenarios/measurement`, `Measurement.tsx`; helpers in `measurementFormat.ts`,
  vitest) shows coverage, decision impact and the event replay, each saying when its report is missing.
- **Design system:** semantic Tailwind tokens → CSS vars in `src/index.css`; theme-aware charts via
  `lib/chartTheme.ts` + `components/shared/EChart.tsx`; dark mode via `[data-theme="dark"]`.
  **Convention: never use raw `slate-*`** — always a role token (`surface`/`fg`/`border`/`primary`/…).

## BFF (`ui/bff/app`)

Modules: `main.py` (declares all `/api` routes), `settings.py`, `stores.py` (DuckDB over Parquet),
`pricing.py`, `risk.py`, `marketdata.py`, `md_consumption.py` (feed→consumption registry),
`options_signals.py` (P6/B6 option-implied signal reads: `/api/options/signals[/credit|/{name}|/{name}/rnd]`),
`news.py` (P6/A5 news reads: `/api/news/events`, `/calibration`, `/{ticker}` — 8-K stream + resolved
scenariogen news calibration via `NewsEventProvider`),
`scenarios.py` (incl. step 15f: `/api/scenarios/ablation[/{name}]` — layer-ablation waterfall + grouped
Shapley + cost per step from the newest `ablation_*.json`, `?severity=&run_id=`; `/api/scenarios/signal-health`
— per-source status, held-out tiers, stopped scenarios, `attention`; pure logic in
`src/scenariogen/report_views.py`; replay drivers/horizon via `definition_view`, so a tier not in the
date's reports returns `outcome` + `definition`; `/api/scenarios/measurement?valuation_date=` — coverage +
decision-impact views of the date's newest `coverage_*` / `decision_*.json` and the newest
`data/scenariogen/event_replay/event_replay_*.json` overall, each block `{"available": false, "reason"}` when
missing or invalid; these routes sit before `/api/scenarios/{name}`),
`system.py`, `governance.py`, `sizing.py`, `llmops.py`, `jobs.py` (whitelist + gate;
execution + status records delegate to `src/common/jobs` — `LocalSubprocessRunner` locally, SQS
`AwsJobRunner` in aws mode), `security.py` (write-gate + RBAC), `audit.py`. The OpenAPI at
`:8088/docs` is the authoritative route list.

**4 gated writes** (`require_write(action)` — token + `configs/ui/roles.json` RBAC, all audited to
`data/pricer/audit/audit.jsonl`): `POST /risk/limits`, `POST /scenarios/signoff`, `POST /jobs`,
`POST /sizing/activate` (writes `src/pricer/portfolio/portfolio.json`).

## Gotchas

- **Pages are empty until the runners populate `data/`.** Base layer is `python -m src.pricer.runner`;
  then lineage_runner / risk_runner / scenariogen.runner / sizing_runner / governance_runner /
  marketdata.* + the LLM flow for `/llmops`. See the prerequisite chain in `ui_guide.md`.
- **The BFF image has no QuantLib** (`infra/bff/Dockerfile` strips the wheel — it's the compute-free
  image, heavy work goes to the backend via the JobRunner seam). So every `src.*` the BFF imports,
  **including deferred in-handler imports**, must resolve QuantLib-free — watch for eager package
  `__init__` re-exports, which is how `src/scenariogen/__init__.py` once broke container startup.
  `tests/bff/test_bff_quantlib_free.py` enforces it.
- `ui/` is **not** in the mypy/ruff `files=` gate (that's `src/*`); the FE has its own `tsc`/vitest.
- The `ui/docs/*` build-plan docs are historical (V1 is complete) — trust the code + `ui_guide.md`.
