# Faultline — User & Operator Guides

This folder is the **operational handbook** for the Faultline platform: a QuantLib‑based
Financial Portfolio Analytics system that downloads market data, prices a multi‑asset book,
computes risk (sensitivities / VaR / scenarios), generates correlated stress scenarios, and
serves it all through a React UI.

Each guide is written for **two audiences**:

- 👔 **Business users** — portfolio managers, risk analysts, governance/validation. *What the
  system tells you and how to read it.*
- 🛠️ **Technical operators** — engineers running batch jobs, the API service, and the UI. *Exact
  commands, flags, paths, and troubleshooting.*

---

## The subsystems

Four data-pipeline subsystems feed a React UI:

| Subsystem | What it does | Guide |
|---|---|---|
| 📥 **Market Data** | Downloads rates / equity / FX / credit / bonds / options / stress data from ~two dozen free public sources into CSVs, then quality‑gates them. | [marketdata_guide.md](marketdata_guide.md) |
| 💰 **Pricer** | Prices the 38‑trade portfolio with QuantLib (PV + measures), with a results store, batch runner, and a FastAPI pricing service. Includes **Risk** and **Portfolio Construction/sizing**. | [pricer_guide.md](pricer_guide.md) |
| 📊 **Risk** | Sensitivities (Greeks), historical + full‑reval VaR / ES, named stress scenarios, P&L explain, VaR backtest, JTD/IDR, and limits. Ships inside the pricer module. | [risk_guide.md](risk_guide.md) |
| 🌩️ **ScenarioGen** | Correlated, covariance‑driven stress scenarios with ES‑under‑shock capital, plausibility gate, versioning, sign‑off, and reverse stress. Drivers come from static config, the **deterministic dual‑RAG** path (MacroRAG + ARag), or the **agentic LLM** path. | [scenariogen_guide.md](scenariogen_guide.md) · [scenariogen_llm_flow.md](scenariogen_llm_flow.md) |
| 🖥️ **UI** | Vite+React explorer (`ui/app`) over a FastAPI backend‑for‑frontend (`ui/bff`). Business + Technical lenses, calculation lineage; read layer + a few gated writes. | [ui_guide.md](ui_guide.md) |

> **Where do "Risk" and "Portfolio Construction" live?** Both are part of the pricer codebase
> (`src/pricer/risk/`, `src/pricer/sizing/`) and run with `python -m src.pricer.risk_runner` /
> `python -m src.pricer.sizing.sizing_runner`. Risk has its own guide as a first‑class capability;
> sizing is covered in [pricer_guide.md](pricer_guide.md) and
> [portfolio_construction_design.md](../design/portfolio_construction_design.md).

---

## Pricing methodology & per‑trade write‑ups

Where the guides above tell you *how to operate* the pricer, [docs/pricing/](../pricing/README.md)
explains *how every trade is actually priced* — what market data each instrument conceptually
needs, what free‑source data we actually feed it (and the documented assumptions where no free
data exists), and the step‑by‑step QuantLib method. Each page is layered: plain‑English first,
quantitative detail second.

| Resource | What it covers |
|---|---|
| [docs/pricing/README.md](../pricing/README.md) | Index of all 38 trades → their write‑ups, plus the "add a new trade" workflow |
| [docs/pricing/methodology/](../pricing/README.md#methodology-library) | Deep, reusable methodology by instrument type (SOFR multi‑curve, swaptions, Bermudan/Hull‑White, options, bonds, CDS, FX) |
| [docs/pricing/trades/](../pricing/README.md#per-trade-write-ups) | One 1–2 page write‑up per trade (economics + data + method + live diagnostics) |
| [docs/pricing/TEMPLATE.md](../pricing/TEMPLATE.md) | Copy‑me template to document any new trade |

---

## Platform dependencies at a glance

| Dependency | Required? | Used by | Notes |
|---|---|---|---|
| **Python 3.12 (conda env `py312`)** | ✅ Yes | everything | Run everything with `conda run --no-capture-output -n py312 …`. QuantLib is the PyPI wheel installed **into** this env (not a conda-forge package), so it still only imports inside `py312`. |
| **QuantLib 1.42.1** | ✅ Yes | pricer, risk, scenariogen | **Version is part of the model definition** — stamped into every result's provenance and pinned (the `QuantLib` PyPI wheel) in [requirements.txt](../../requirements.txt); the container images use the same wheel. Do not bump casually. |
| **Node.js v24.14.0** | UI only | `ui/app` | Node v24.14.0 on PATH, or call its `npm.cmd` by full path. |
| **Relational DB server** | ❌ **No** | — | The platform is **fully file‑based**. There is no Postgres/MySQL/etc. |
| **Parquet + DuckDB** | ✅ Yes (embedded) | pricer/risk/scenariogen stores, BFF | DuckDB is an **in‑process** SQL engine over Parquet files under `data/`. No server. |
| **Vector database** | ❌ Not in this repo | (MacroRAG only) | ScenarioGen's optional MacroRAG **service** uses a vector DB *internally*; this repo only calls its REST API. |
| **LLM API keys** | Optional | scenariogen `--macro` | See [scenariogen_guide.md](scenariogen_guide.md). Everything else runs with **no LLM**. |
| **Market‑data API keys** | Partly | marketdata | Most sources need no key; a few do — see below. |

### Storage model (no database server)

All `data/` I/O routes through a **dual-mode storage façade** (`src/common/storage/`): with the
defaults below it is the local tree shown here, byte-for-byte as before; set `PRICER_DEPLOY_MODE=aws`
+ `PRICER_DATA_URI=s3://bucket/prefix` and the *same* code/images read and write S3 instead. See the
[deployment guide](deployment_guide.md).

```
data/   (= PRICER_DATA_URI; default <repo>/data, or an s3:// prefix)
├── marketdata/<asset_class>/*.csv      ← raw downloaded feeds (+ .provenance.json sidecars)
├── pricer/
│   ├── store/results/  (Parquet, hive-partitioned)  ← queried via DuckDB
│   ├── store/runs/     (Parquet run manifests)
│   ├── store/spans/    (per-run timings)
│   ├── PV/<asset>/<trade>/<date>.json  ← per-trade results
│   ├── lineage/<date>/<trade>.json     ← calculation lineage
│   ├── risk/<date>/risk_<run>.json     ← risk reports
│   ├── scenario/<date>/scenario_<run>.json ← stress reports
│   ├── governance/<date>/…             ← golden/IPV/inventory
│   └── snapshots/<snapshot_id>.json    ← content-addressed market-data fingerprints
├── risk/        and  scenariogen/       ← module run manifests
```

---

## One‑time setup

```powershell
# 1. Activate the Python environment (conda is on the PowerShell PATH, not Bash)
conda activate py312
# …or prefix every command:  conda run --no-capture-output -n py312 <command>

# 2. Install Python dependencies (pinned)
pip install -r requirements.txt            # runtime core
pip install -r requirements-service.txt    # + FastAPI/uvicorn (for the pricing service & BFF)
pip install -r requirements-dev.txt         # + pytest/ruff/mypy (for development/CI)

# 3. Create a .env in the repo root (gitignored) with any API keys you have — see table below.

# 4. From the repo root, set PYTHONPATH for module-style runs when needed
$env:PYTHONPATH = $PWD.Path   # the repo root
```

### Environment variables / secrets

Put these in `.env` at the repo root (it is **gitignored** — never commit secrets).

| Variable | Subsystem | Required? | Get it from |
|---|---|---|---|
| `STOOQ_API_KEY` | marketdata · equity | Optional — Stooq needs no key; only the disabled Nasdaq source uses it | Nasdaq Data Link |
| `FINRA_API_KEY` | marketdata · bonds | Needed for FINRA bond data | FINRA API console (OAuth2 client id) |
| `FINRA_API_KEY_SECRET` | marketdata · bonds | Needed with the above | FINRA API console (OAuth2 secret) |
| `EIA_API_KEY` | marketdata · stress | Optional (skips source if absent) | eia.gov/opendata (free) |
| `OPENFIGI_API_KEY` | marketdata · reference | Optional (anonymous tier works) | openfigi.com |
| `FRED_API_KEY` | marketdata · rates/stress | **Not needed** (uses free CSV endpoint) | — |
| `MACRORAG_API_KEY` / `ARAG_API_KEY` | scenariogen · `--macro` / `--dual-rag` | Optional (only if the service enforces auth) | your MacroRAG / ARag service |
| `MACRORAG_BASE_URL` / `ARAG_BASE_URL` · `ARAG_HOME` / `MACRORAG_HOME` | scenariogen RAG paths | Optional (defaults to localhost / sibling repos) | relocate the RAG services in one place (F6) |
| `OPENAI_API_KEY` | scenariogen LLM path + MacroRAG service | Optional | Required for `llm.runner` under the `openai` profile (the default); also used by MacroRAG itself |
| `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` | scenariogen LLM path | Optional | Required for `llm.runner` under the `anthropic` / `deepseek` profile ([LLM flow guide](scenariogen_llm_flow.md)) |
| `SCENARIOGEN_LLM_PROFILE` | scenariogen LLM path | Optional (default `openai`) | provider profile from `configs/scenariogen/llm/llm.json`; `--llm-profile` overrides |
| `LOG_LEVEL` / `PRICER_LOG_LEVEL` | all | Optional (default `INFO`) | — |
| `PRICER_ENV` | all | Optional (default `dev`) | selects `*.<env>.json` config overlay |
| `PRICER_WORKERS` | pricer/risk | Optional (default `1`) | parallelism default |
| `PRICER_DEPLOY_MODE` | all | Optional (default `local`) | `local` (filesystem) or `aws` (S3 + cloud jobs) — see [deployment guide](deployment_guide.md) |
| `PRICER_DATA_URI` | all | Optional (default `<repo>/data`) | data-tree root: a local path or `s3://bucket/prefix` (**required** in `aws` mode) |
| `PRICER_API_KEY` | pricer service | Optional (default off) | when set, the FastAPI facade requires an `X-API-Key` header (no-op when unset) |
| `QLP_CORS_ORIGINS` | UI/BFF | Optional (default dev origins) | comma-separated allowed CORS origins (the ALB hostname in AWS) |
| `JOBS_QUEUE_URL` | UI/BFF (`aws`) | Optional | SQS queue the `AwsJobRunner` enqueues to for a backend worker |

---

## The end‑to‑end run order

Each subsystem reads the previous one's output from `data/`. A full daily cycle:

```powershell
$env:PYTHONPATH = $PWD.Path   # the repo root

# 1. Market data — download/refresh all domains, then quality-gate
python -m src.marketdata.rates.runner
python -m src.marketdata.equity.runner
python -m src.marketdata.fx.runner
python -m src.marketdata.bonds.runner
python -m src.marketdata.credit.runner
python -m src.marketdata.options.runner
python -m src.marketdata.stress.runner
python -m scripts.marketdata.dq_gate            # PASS/FAIL gate

# 2. Pricing — value the book (writes the snapshot every downstream step pins to)
python -m src.pricer.runner

# 3. Risk + Scenarios + Governance + Lineage (all read the priced snapshot)
python -m src.pricer.risk_runner
python -m src.scenariogen.runner
python -m src.pricer.governance_runner
python -m src.pricer.lineage_runner

# 4. UI (two long-running processes) — see the UI guide
conda run --no-capture-output -n py312 uvicorn ui.bff.app.main:app --port 8088 --reload
# (separate terminal) npm run dev   # in ui/app, Node v24.14.0
```

---

## Containers & deployment

To run the whole stack in containers (`docker compose up`), point the platform at **S3 instead of
the local filesystem** (the `deploy_mode` switch), enable the pricer service's API key, or deploy to
AWS — see the **[deployment guide](deployment_guide.md)**. Local bare-metal (above) needs none of it.

---

## Design documents (deep background)

The guides summarize operations; these design docs explain the *why*:

| Document | Topic |
|---|---|
| [AITrade_quantlib_pipeline_design.md](../design/AITrade_quantlib_pipeline_design.md) | 3‑layer pricing architecture, reproducibility, P0–P5 roadmap |
| [AITrade_quantlib_portfolio_marketdata.md](../design/AITrade_quantlib_portfolio_marketdata.md) | Portfolio scope (originally 30 trades; now 38 — see [portfolio_rebalance_review.md](../design/portfolio_rebalance_review.md)), data sources, cadence |
| [AITrade_risk_data_gap_analysis.md](../design/AITrade_risk_data_gap_analysis.md) | Market‑data gaps and free‑data substitutions |
| [AITrade_stresstest_scenario_def_design.md](../design/AITrade_stresstest_scenario_def_design.md) | Correlated stress scenario design |
| [scenariogen_named_scenario_dualrag_design.md](../design/scenariogen_named_scenario_dualrag_design.md) | Deterministic dual‑RAG (MacroRAG + ARag) enrichment |
| [scenariogen_llm_flow_design.md](../design/scenariogen_llm_flow_design.md) | Agentic LLM scenario‑generation flow |
| [AITrade_market_intelligence_design.md](../design/AITrade_market_intelligence_design.md) | **Proposal — sequenced (§11); P0 delivered 2026‑07‑24 (§11.8)** — free news/events (SEC 8‑K + GDELT), options‑market signals, and ten further factor classes (liquidity, funding, basis, style, crowding) for scenariogen |
| [MacroRAG_synthesis_endpoint_spec.md](../design/MacroRAG_synthesis_endpoint_spec.md) | MacroRAG REST contract (LLM/RAG narrative layer) |
| [AITrade_pricing_enhancement_design.md](../design/AITrade_pricing_enhancement_design.md) | Pricing‑quality enhancement program (A→B→C) |
| [portfolio_construction_design.md](../design/portfolio_construction_design.md) | Risk‑appetite position sizing |
| [AITrade_production_hardening_design.md](../design/AITrade_production_hardening_design.md) | Cross‑module operability backlog |
| [AITrade_aws_deployment_design.md](../design/AITrade_aws_deployment_design.md) | **AWS dual-mode (local FS + S3) deployment** — storage façade, containers, API auth, VPC/EC2 topology, phased change list (Phases A–D delivered) |
| [AITrade_deployment_runbook_design.md](../design/AITrade_deployment_runbook_design.md) | Deploy/ops layer (gateway, orchestration, monitoring) — K8s blueprint, complemented by the AWS design above |
| [harness_audit_202607.md](../design/harness_audit_202607.md) | AI‑harness audit of the XBRL/RAG flow (F1–F7) |

UI design docs live under [ui/docs/](../../ui/docs/) (`BUILD_PLAN.md`, `UI_DESIGN.md`, per‑subsystem UI specs).

---

## Cross‑cutting troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: src…` | Wrong env or missing `PYTHONPATH` | Run inside `py312`; run from the repo root and set `$env:PYTHONPATH = $PWD.Path`. |
| `conda: command not found` (in Bash) | conda is only on the **PowerShell** PATH | Use PowerShell, or call the full conda path. |
| UI pages are empty | Batch runners haven't produced data | Run the pricer (and risk/scenario) runners first — see each guide's "what lights up the UI". |
| Pricing/risk says data is stale or blocked | Market data old or missing | Re‑run the relevant `src.marketdata.*.runner`, then `dq_gate`. |
| Numbers changed unexpectedly between runs | QuantLib version or config drift | Check governance (golden PV) report; QuantLib version is pinned for this reason. |
| `ImportError: QuantLib` | Not in the active env | The QuantLib **wheel** is installed into `py312` (`pip install -r requirements.txt`) — ensure you're in that env. |

See each subsystem guide for domain‑specific troubleshooting.
