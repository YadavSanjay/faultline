# 📊 Risk — User & Operator Guide

> Computes **sensitivities (Greeks)**, **historical VaR / Expected Shortfall**, **named stress
> scenarios**, **P&L attribution**, a **VaR backtest**, and **limit monitoring** for the
> portfolio. Risk ships inside the pricer module (`src/pricer/risk/`) and reuses the same
> `PricingEngine`, snapshot, and curve cache — so risk and pricing never disagree.

Source: [src/pricer/risk/](../../src/pricer/risk/) · Runner: [src/pricer/risk_runner.py](../../src/pricer/risk_runner.py) · Config: `risk` block in [configs/pricer/pricer_config.json](../../configs/pricer/pricer_config.json) · Design background: [AITrade_risk_data_gap_analysis.md](../design/AITrade_risk_data_gap_analysis.md)

---

## 👔 For business users

### What you get from one risk run

| Output | What it tells you |
|---|---|
| **Sensitivities** | Per‑trade and aggregate Greeks: IR key‑rate (1M–30Y), CS01 (credit), equity delta/gamma, FX delta/rho, rates & equity vega (incl. term/skew). |
| **Stress scenarios** | Full‑revaluation P&L under named shocks: `rates_selloff_100bp`, `credit_blowout`, `taiwan_geopolitical`, `ai_capex_bust`, `ai_capex_boom`. |
| **VaR / ES** | Historical‑simulation Value‑at‑Risk and Expected Shortfall at 95% / 97.5% / 99% (97.5% ES is the FRTB measure). Sensitivity (delta‑gamma) VaR **plus** full‑revaluation VaR (on by default), **Filtered HS** (EWMA vol‑scaled), **Stressed VaR/ES** (Basel 2.5), a **parametric** Gaussian cross‑check, and **component‑ES decomposition** (Euler, by factor class). |
| **P&L explain** | Decomposition of a scenario's P&L into delta / gamma / vega / rho / FX + unexplained residual, with a coverage %. Plus **carry/theta** 1‑day predicted P&L. |
| **VaR backtest** | Rolling exception count, Kupiec POF + **Christoffersen** independence/conditional‑coverage tests, and Basel traffic‑light zone — run on both sensitivity‑VaR and full‑reval P&L vectors. |
| **JTD / IDR** | Jump‑to‑default / incremental default risk per issuer (simplified FRTB‑DRC), IG/HY bucketed. |
| **Limits** | Breaches of configured VaR and sensitivity limits (logged + recorded; they don't fail the run). |

### How to read VaR vs scenarios
- **VaR/ES** is statistical (what could a *normal bad day* lose, from history).
- **Scenarios** are narrative and severe (what does *this specific story* lose, fully repriced).
- **Sensitivity‑based VaR** is the fast delta‑gamma view; **full‑reval VaR** is the heavy,
  convexity‑exact version and is **on by default** (disable with `--no-full-reval-var`).

---

## 🛠️ For technical operators

### Prerequisite

A priced portfolio context (risk uses the same market‑data snapshot as the pricer). Run the
market‑data runners and ideally the pricer first — see [pricer_guide.md](pricer_guide.md).

### Runner

```powershell
$env:PYTHONPATH = $PWD.Path   # the repo root
python -m src.pricer.risk_runner [flags]
```

| Flag | Default | Meaning |
|---|---|---|
| `--asset-class` | full book | Restrict to one asset class. |
| `--valuation-date` | portfolio default | ISO date. |
| `--no-scenarios` | off | Skip named stress scenarios. |
| `--no-var` | off | Skip historical VaR. |
| `--no-full-reval-var` | off (full‑reval VaR **ON**) | Skip the heavy full‑revaluation historical VaR (`n_days × n_trades` repricings). |
| `--no-key-rates` | off | Skip per‑tenor key‑rate buckets (faster). |
| `--explain SCENARIO` | none | Add P&L attribution for one scenario. |
| `--workers N` | `PRICER_WORKERS` or 1 | Processes for full‑reval VaR. |
| `--no-backtest` | off | Skip the VaR backtest. |
| `--book` | active `portfolio.json` (= `core`) | Price a specific book (`base`/`balanced`/`core`/`aggressive`/`risky`). |

```powershell
python -m src.pricer.risk_runner                                  # full risk pack
python -m src.pricer.risk_runner --asset-class rates --no-var
python -m src.pricer.risk_runner --explain taiwan_geopolitical
python -m src.pricer.risk_runner --workers 4          # full‑reval VaR (default) parallelised
python -m src.pricer.risk_runner --no-full-reval-var  # sensitivity VaR only (fast)
```

### Output

A single provenance‑stamped JSON: `data/pricer/risk/<valuation_date>/risk_<run_id>.json`,
containing sensitivities, scenario P&Ls, VaR/ES (incl. the historical `pnl_vector` for the UI
histogram), optional full‑reval VaR, optional P&L explain, limit breaches, and the backtest. A
run manifest is also written to the shared `store/runs/`.

### On‑demand via the pricing service

The FastAPI service exposes risk endpoints (start it per [pricer_guide.md](pricer_guide.md)):

```http
POST /risk/sensitivities   {trade_id, valuation_date?, key_rates?}
POST /risk/scenario        {scenario, asset_class?, valuation_date?}
POST /risk/var             {asset_class?, valuation_date?}
```

---

## ⚙️ Configuration (the `risk` block)

In [configs/pricer/pricer_config.json](../../configs/pricer/pricer_config.json):

- **Bumps**: `ir_bump_bp`, `credit_bump_bp`, `equity_vol_bump`, `spot_bump_pct`, `fx_bump_pct`
  (central bump‑and‑revalue).
- **`scenarios`**: each named scenario's shocks (`ir_bp`, `credit_bp`, `equity_pct`,
  `equity_vol`, `fx_pct`, …).
- **`var`**: `confidence_levels` `[0.95, 0.975, 0.99]`, `horizon_days` 1, `lookback_days` 250,
  and a `factors` map binding 39 historical series (e.g. `DGS1MO` → `IR_KR:1M`) to book
  sensitivities.
- **`limits`**: e.g. `var.0.99`, `sensitivities.CS01`, `sensitivities.IR_PARALLEL`.
- **`backtest`**: `level` (0.99), `window` (250), `test_days` (250).

---

## 🧠 Methodology reference

| Component | File | Method |
|---|---|---|
| Sensitivities | [risk/sensitivities.py](../../src/pricer/risk/sensitivities.py) | Central bump‑and‑revalue per factor. |
| Scenarios | [risk/scenarios.py](../../src/pricer/risk/scenarios.py) | Apply all transforms simultaneously, full reval. |
| Sensitivity VaR/ES | [risk/var.py](../../src/pricer/risk/var.py) | Delta‑gamma HS + Filtered HS + Stressed VaR + parametric x‑check + component‑ES. |
| Full‑reval VaR | [risk/historical_var.py](../../src/pricer/risk/historical_var.py) | Replay each day's co‑moves, fully reprice the book. |
| Factor history | [risk/factor_history.py](../../src/pricer/risk/factor_history.py) | Aligned factor‑move panel + PIT snapshot behind full‑reval VaR. |
| Backtest | [risk/backtest.py](../../src/pricer/risk/backtest.py) | Rolling exceptions, Kupiec POF, Christoffersen, Basel zone. |
| JTD / IDR | [risk/jtd.py](../../src/pricer/risk/jtd.py) | Jump‑to‑default / incremental default risk per issuer (simplified FRTB‑DRC). |
| Limits | [risk/limits.py](../../src/pricer/risk/limits.py) | Compare aggregates to configured limits. |
| P&L explain | [risk/pnl_explain.py](../../src/pricer/risk/pnl_explain.py) | Greeks decomposition + residual + coverage + carry/theta. |

---

## 🔗 How other subsystems relate

- **ScenarioGen** is the *correlated, covariance‑driven* extension of these named scenarios — it
  reuses the same bump transforms and the full‑reval VaR engine for ES‑under‑shock capital. See
  [scenariogen_guide.md](scenariogen_guide.md).
- **The UI** renders the risk JSON as a dashboard (VaR histogram, sensitivity drill‑downs, P&L
  explain, backtest, limits) via `/api/risk/*` — see [ui_guide.md](ui_guide.md). The risk
  report must exist for those pages to populate.

---

## 🚑 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `missing_factors` listed in VaR result | Book has exposure but no history file for that factor | Download the relevant market‑data series; check the `var.factors` mapping points to a present CSV/column. |
| Full‑reval VaR very slow | `n_days × n_trades` repricings on 1 worker | Add `--workers N`; or use sensitivity VaR (drop `--full-reval-var`). |
| Sensitivities fail for some trades | Trade type doesn't support a bump, or input gap | Check `risk_sensitivities_failed_total`; isolate with `--asset-class`. |
| VaR looks implausibly small/large | Wrong `lookback_days`, or a flat/short factor history | Verify the factor CSVs cover the lookback window with real daily moves. |
| Backtest zone is red | Genuine exceptions, or a window/level mismatch | Confirm `backtest.level`/`window`; investigate the exception days before assuming a bug. |
| Limit breach but run still "ok" | By design — limits are recorded, not fatal | Breaches appear in the report and `risk_limit_breach_total`; act per your governance process. |
| P&L explain coverage low | Large convexity/cross terms the Greeks miss | Expected for severe scenarios; the residual is the non‑linear part. |

**Common to risk systems:** VaR is backward‑looking — a quiet lookback understates tail risk;
pair it with the **scenarios** for forward‑looking severity. Sensitivities are *local* (small
bumps) and won't capture gamma in a big move — that's what full‑reval VaR and scenarios are for.
