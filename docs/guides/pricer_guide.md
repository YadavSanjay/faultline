# 💰 Pricer — User & Operator Guide

> Prices the 38‑trade, multi‑asset portfolio with **QuantLib 1.42.1**. Runs as a batch
> (`runner.py`) or as a **FastAPI service** (`service.py`), writes results to a **Parquet +
> DuckDB** store, and stamps every result with full provenance (git SHA, QuantLib version,
> config hash, market‑data `snapshot_id`).

Source: [src/pricer/](../../src/pricer/) · Config: [configs/pricer/](../../configs/pricer/) · Portfolio: [src/pricer/portfolio/](../../src/pricer/portfolio/)

> 📚 **How is each trade actually priced?** This guide covers *operating* the pricer. For the
> per‑instrument pricing methodology and a 1–2 page write‑up of every trade (market data, models,
> live diagnostics), see [docs/pricing/](../pricing/README.md).

---

## 👔 For business users

### What it produces

- A **PV (present value)** and supporting measures for every trade, aggregated by asset class
  and for the whole book.
- A **point‑in‑time, reproducible mark**: each run pins the exact market‑data snapshot, so the
  same inputs always produce the same number — and you can prove it (provenance).
- **PV‑drift alerts**: if a trade's PV moves more than the configured tolerance versus its prior
  mark, the run flags it.
- **Calculation lineage** (via `lineage_runner`): a plain‑English, inputs→intermediates→final
  explanation of how each PV was built — visible in the UI.

The portfolio is an AI‑themed book of 38 trades spanning cash equity, ETFs, equity options, SOFR
swaps, swaptions, caps, corporate & Treasury bonds, single‑name & index CDS, and FX forwards.
The default valuation date and type live in [src/pricer/portfolio/](../../src/pricer/portfolio/) — the
default is now **2026-10-06** (the live SOFR curve date; the whole book incl. credit prices there).

> **Position sizes are mandate-driven, and the book is selectable.** The canonical unsized book is
> `portfolio_base.json` (~$50mm placeholders); the [Portfolio Construction](../design/portfolio_construction_design.md)
> module (`src/pricer/sizing/`) sizes it into the four `portfolio_{balanced,core,aggressive,risky}.json`
> variants, and the default (`portfolio.json`) is the **core (Aggressive-Balanced)** variant. The
> optimizer scales every book to its VaR budget, caps each single name's realized risk share, and pins
> beta-neutral long/short pairs — see [portfolio_construction_design.md](../design/portfolio_construction_design.md).
> Select any with `--book` (see below). Re-derive all variants with `python -m src.pricer.sizing.sizing_runner`.

---

## 📈 Pricing methodology & recent enhancements

The per‑instrument methodology (models, market data, live diagnostics) lives under
[docs/pricing/methodology/](../pricing/README.md); the governing plan for the enhancement
program is [AITrade_pricing_enhancement_design.md](../design/AITrade_pricing_enhancement_design.md).

The engines are idiomatic QuantLib and were already sound. The **2026‑07‑12 enhancement program**
(A→B→C, 14 items) sharpened the *inputs* — curves, volatility, and credit/foreign term structures —
mostly using data already downloaded, and documented the honest boundaries where free data runs out.

| Area | Before | Now | Doc |
|---|---|---|---|
| SOFR curve | flat past the 10Y node | real 12Y/15Y/20Y nodes + a constant‑forward 30Y (long bonds & the 5Y×10Y swaption no longer flat‑extrapolated) | `01-sofr-multicurve` |
| Single‑name CDS | flat hazard from one 5Y point | piecewise hazard **curve** bootstrapped where the feed has ≥2 tenors (ORCL) | `50-credit-cds` |
| Corporate bonds | single flat Z‑spread | issuer Z‑spread **term structure** + a maturity‑interpolated SOFR‑Treasury basis | `20-bonds` |
| American options | continuous dividend yield | **discrete ex‑dividends** (forward‑preserving) → correct early‑exercise timing | `11-equity-options` |
| Equity vol | flat vol to the FD grid | full **BlackVarianceSurface** built & shown (Explain heatmap); each leg still prices on its exact skew point | `11-equity-options` |
| USD/TWD spot | Fed H.10 (went stale ~05‑29) | **CBC NDF fixing** — the settlement reference, and fresh | `40-fx` |
| CDX index | single‑name flat‑hazard proxy | explicit **untranched index** model + an index‑factor hook | `50-credit-cds` |
| CDS risk | CS01 only | + **rec01** (recovery sensitivity) | `50-credit-cds` |
| Rates vol | one flat MOVE number | **expiry‑dependent** MOVE‑anchored term structure → gives the Bermudan calibration a real slope (identifies Hull‑White mean reversion) | `31/32/33` |
| XCCY basis | assumed zero | config **hook** (`xccy_basis_bp`, default 0 — a licensed basis drops in) | `40-fx` |
| UST marks | — | **FINRA TRACE** traded‑price cross‑check for the on‑the‑run 10Y/2Y | `20-bonds` |

**Known data boundaries — documented, not hidden.** Free data supports the correct first‑order
treatment but not full sophistication, so these are held as explicit, revertable assumptions in the
methodology docs and [model_inventory.json](../../configs/pricer/model_inventory.json):

- **No swaption smile/cube** — the DTCC SDR IR‑options feed isn't calibratable; rates vol is flat
  across strike, and its expiry term structure is a *stylized* MOVE‑anchored shape (a modeling
  assumption, not a market surface — set `rates.normal_vol.term_structure` to `{}` to revert to flat).
- **Equity local‑vol FD not used** — Dupire local vol from the sparse free chain is unstable
  (over‑priced a 1‑month straddle ~8%); the surface is built and *shown*, but each leg prices on its
  exact (strike, expiry) IV.
- **XCCY basis = 0**, **foreign curves flat** (TONAR is overnight‑only; the TWD NDF feed is spot‑only),
  **realized‑SOFR fixings not wired** (latent — every trade spot‑starts), and **no callable/OAS**
  (all bonds are bullets). Each has a ready hook or a documented path for when data/trades appear.

Every change is reflected across all four explain surfaces: the **methodology** tab, the
**Calculation Lineage** (with new hazard‑curve, credit‑spread‑curve and vol‑surface charts), the
**model inventory**, and the per‑trade **"This trade"** pages — and the governance golden/IPV gates
stay green (goldens re‑baselined with a review note per change in
[golden_pvs.json](../../configs/pricer/golden_pvs.json)'s `rebaseline_history`).

---

## 🛠️ For technical operators

### Prerequisite

Market data must exist under `data/marketdata/` (run the market‑data runners first — see
[marketdata_guide.md](marketdata_guide.md)). The pricer reads it through a snapshot and
will **warn or block** on stale/missing data per `data_quality` policy in the config.

### Batch runner

```powershell
$env:PYTHONPATH = $PWD.Path   # the repo root
python -m src.pricer.runner [flags]
```

Source: [src/pricer/runner.py](../../src/pricer/runner.py).

| Flag | Default | Meaning |
|---|---|---|
| `--asset-class` | full portfolio | Price only one asset class, e.g. `rates`. |
| `--trade` | all | Price a single trade id, e.g. `TRADE_22`. |
| `--valuation-date` | portfolio default | ISO date to value at. |
| `--valuation-type` | `PV` | Valuation type. |
| `--workers` | `PRICER_WORKERS` or 1 | Process workers for parallel pricing. |
| `--book` | `core` (`portfolio.json`) | Risk-appetite book to price: `base`\|`balanced`\|`core`\|`aggressive`\|`risky` (or set `PRICER_BOOK`). |

```powershell
python -m src.pricer.runner                              # full book (default = core)
python -m src.pricer.runner --asset-class rates --workers 2
python -m src.pricer.runner --trade TRADE_22
python -m src.pricer.runner --valuation-date 2026-10-06 --workers 4
python -m src.pricer.runner --book balanced             # price the balanced book variant
```

> **Book as a variable.** The book is selectable. `portfolio.json` is the *active default* (a copy
> of the **core** variant); the canonical unsized **base** book is `portfolio_base.json`, and the
> four risk-appetite variants are `portfolio_{balanced,core,aggressive,risky}.json`. `--book`, the
> `PRICER_BOOK` env var, and the `book=` loader argument all select one (default: core). The
> sizing runner regenerates the variants from the base. `--book` is available on `runner`,
> `risk_runner`, `lineage_runner`, and `scenariogen.runner`.

**Parallelism & curve cache**: trades are sorted by `(asset_class, trade_id)` and chunked across
`--workers` processes for **curve‑cache locality** — each distinct curve (e.g. the SOFR curve) is
bootstrapped once per worker and reused across that worker's trades (see
[src/pricer/ql/common/curve_cache.py](../../src/pricer/ql/common/curve_cache.py)). For a rates book this
cuts ~30 curve builds down to a handful.

### Outputs

| Output | Path |
|---|---|
| Parquet results store (DuckDB‑queryable) | `data/pricer/store/results/` (hive‑partitioned by `valuation_type`/`valuation_date`/`asset_class`) |
| Per‑trade JSON | `data/pricer/PV/<asset_class>/<trade_id>/<date>.json` |
| Run manifest (audit) | `data/pricer/store/runs/` |
| Per‑run span timings | `data/pricer/store/spans/<run_id>.json` |
| Market‑data snapshot | `data/pricer/snapshots/<snapshot_id>.json` |
| Prometheus textfile | `logs/metrics/pricer.prom` |

### FastAPI pricing service

Serve on‑demand pricing and query the stored results over HTTP. Requires
`requirements-service.txt`.

```powershell
conda run --no-capture-output -n py312 uvicorn src.pricer.service:app --host 0.0.0.0 --port 8080
```

Source: [src/pricer/service.py](../../src/pricer/service.py). One `PricingEngine` is warmed at startup
so intraday requests and EOD batch share identical code paths.

| Method · Path | Purpose |
|---|---|
| `GET /health` | liveness + `snapshot_id` + QuantLib version |
| `POST /price` | price one trade `{trade_id, valuation_date?, valuation_type?}` |
| `POST /price-portfolio` | price a filtered book |
| `GET /results/pv-history/{trade_id}` | PV per date (DuckDB) |
| `GET /results/book/{valuation_date}` | book PV by asset class |
| `GET /results/run/{valuation_date}` | latest run results |
| `POST /risk/sensitivities` · `POST /risk/scenario` · `POST /risk/var` | risk on demand (see [risk_guide.md](risk_guide.md)) |
| `GET /runs` · `GET /portfolio/versions` | audit trail / portfolio versions |
| `GET /governance/model-inventory` · `POST /governance/verify` | governance |
| `GET /metrics` | Prometheus exposition |

**Authentication.** Set `PRICER_API_KEY` and the service requires a matching `X-API-Key` header on
every route except `/health` and `/metrics`; unset ⇒ open (dev parity). The key is read per-request,
so rotation needs no restart. See the [deployment guide](deployment_guide.md#4-api-authentication).

### Querying the results store directly (DuckDB)

No server needed — DuckDB reads the Parquet files in‑process:

```sql
-- e.g. from `duckdb` CLI or python
SELECT asset_class, count(*) n, sum(pv) total_pv
FROM read_parquet('data/pricer/store/results/**/*.parquet')
WHERE valuation_date = '2026-10-06'
GROUP BY asset_class ORDER BY total_pv DESC;
```

### Calculation lineage

```powershell
python -m src.pricer.lineage_runner [--trade TRADE_22] [--asset-class rates] [--valuation-date YYYY-MM-DD]
```

Writes `data/pricer/lineage/<date>/<trade_id>.json`, consumed by the UI's lineage explorer.
Source: [src/pricer/lineage_runner.py](../../src/pricer/lineage_runner.py).

### Governance gate

```powershell
python -m src.pricer.governance_runner [--asset-class …] [--valuation-date YYYY-MM-DD]
```

Runs three controls and **exits 0 (pass) / 1 (fail)** — suitable as a CI/EOD gate:

1. **Golden / regression PVs** vs frozen baseline [configs/pricer/golden_pvs.json](../../configs/pricer/golden_pvs.json) (`rel_tol 1e‑6`, `abs_tol 0.01`; snapshot‑aware).
2. **Independent Price Verification (IPV)** — e.g. par‑swap identity for OIS, accrual identity for bonds.
3. **Model‑inventory coverage** — every trade type must have an entry in [configs/pricer/model_inventory.json](../../configs/pricer/model_inventory.json).

Output: `data/pricer/governance/<date>/governance_<run_id>.json`. Source:
[src/pricer/governance_runner.py](../../src/pricer/governance_runner.py).

> **When golden PVs break:** review the diff. If it's an intended QuantLib bump or market‑data
> change, re‑baseline by updating `golden_pvs.json` (PVs + `snapshot_id` + `quantlib_version`).

---

## ⚙️ Configuration

[configs/pricer/pricer_config.json](../../configs/pricer/pricer_config.json) is the model config:
`max_data_staleness_days`, `observability` (incl. `pv_drift` tolerances), `data_quality`
(`warn|block|substitute` per data kind), and the `risk` block (bumps, scenarios, VaR factors,
limits, backtest). Validation is enforced by Pydantic
([src/pricer/config_models.py](../../src/pricer/config_models.py)) — a mistyped key fails fast with a
`ConfigError`.

**Environment overlay**: an optional `pricer_config.<PRICER_ENV>.json` (e.g. `…prod.json`) is
deep‑merged on top, so per‑env tweaks don't touch the base file. Select it with `PRICER_ENV`.

**Runtime settings** (separate from model params, via `PRICER_*` env vars): `PRICER_ENV`,
`PRICER_LOG_LEVEL`, `PRICER_JSON_LOGS`, `PRICER_WORKERS`, `PRICER_GIT_SHA` (baked into containers),
`PRICER_DEPLOY_MODE` + `PRICER_DATA_URI` (local-FS vs S3), and `PRICER_API_KEY` (service auth). The
deployment ones are covered in the [deployment guide](deployment_guide.md).

---

## 🔗 How other subsystems use the pricer

- **Risk** and **ScenarioGen** import `PricingEngine` and reprice the book under bumped/shocked
  market data — they don't fork pricing logic. See their guides.
- **The UI/BFF** reads the pricer's Parquet store and per‑trade/lineage JSON (read‑only) and also
  *proxies* the live service's `/health` and `/metrics`.
- **Programmatic use:**

```python
from src.pricer.engine import PricingEngine
engine = PricingEngine(config, project_root)
result = engine.price(trade, ctx)          # provenance-stamped PricingResult
outcomes = engine.price_portfolio(trades, ctx)   # shared curve cache, per-trade fault isolation
```

One failing trade returns a `FAILED:`/`BLOCKED:` outcome rather than killing the run.

---

## 🏗️ Containerized / scheduled runs

The current container story is the **qlp-backend** image
([infra/backend/Dockerfile](../../infra/backend/Dockerfile) — `python:3.12-slim` + the QuantLib PyPI
wheel, one multi-entrypoint image for every runner *and* the service, git SHA baked in as
`PRICER_GIT_SHA`) wired with the BFF + UI in the root [docker-compose.yml](../../docker-compose.yml).
Run a batch job with `docker compose run --rm backend python -m src.pricer.runner`. Full details
(images, dual-mode S3, API auth, AWS) are in the **[deployment guide](deployment_guide.md)**.

The older conda-based [infra/pricer/](../../infra/pricer/) K8s templates (`eod-cronjob.yaml` /
`adhoc-job.yaml` + PVC) are **superseded** — retained only for a pure-K8s path. CI:
[.github/workflows/pricer-ci.yml](../../.github/workflows/pricer-ci.yml) (ruff + mypy --strict +
pytest; auto‑run disabled until market data is provisioned in CI).

---

## 🚑 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `BLOCKED: …` outcome for a trade | `data_quality` policy set to `block` and an input is stale/missing | Refresh market data + `dq_gate`; or relax the kind's action to `warn` in the config. |
| `FAILED: …` outcome for one trade | Bad/missing instrument inputs or QuantLib error | Read the per‑trade JSON's status; re‑run with `--trade TRADE_X` to isolate. |
| PV‑drift alerts everywhere | Snapshot changed materially since last run | Expected after a data refresh; confirm against governance/golden. |
| Golden PV breaks in governance | QuantLib bump, refactor, or data change | Review, then re‑baseline `golden_pvs.json` if intended. |
| `ConfigError: unknown field …` | Typo in `pricer_config.json` | Fix the key; Pydantic prints the offending field. |
| Service `/price` returns 422 | Pricing/data error | Body of the 422 explains it; usually missing market data for that trade. |
| Slow full‑book run | Single worker, cold curves | Add `--workers N`; curve cache amortizes across the chunk. |
| Results store query returns nothing | No run yet for that date, or wrong path | Confirm a run wrote `data/pricer/store/results/…`; check `valuation_date`. |

**Common to pricing systems:** snapshot reproducibility means *yesterday's* numbers won't change
when you re‑run today unless you re‑point the valuation date; "missing" PV history usually means
the store was never populated for that date, not that pricing failed.
