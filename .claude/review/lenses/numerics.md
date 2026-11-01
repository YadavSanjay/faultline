---
name: numerics
applies_to: ["src/pricer/**/*.py", "src/marketdata/**/*.py", "configs/pricer/**", "configs/scenariogen/**", "src/common/snapshot.py",
  "src/scenariogen/{attribution,capital,catalog,collateral,covariance,crowding,dualrag,factor_keys,factor_vector,geopolitical,implied_corr,liquidity,plausibility,reverse,scenario_gen,stress,throughput}.py",
  "src/scenariogen/{arag,macro,news}/{calibrate,mapping}.py"]
---
# Quant numerics (QuantLib pricing, risk, scenarios)

A wrong number that looks plausible is the most expensive defect in this repo: it flows
into PVs, VaR, sizing and capital without raising. Hunt for that first.

## Hunt for

**QuantLib state & caching**
- `ql.Settings.instance().evaluationDate` is **process-global**; it is set in
  `pricer/ql/common/base_pricer.py` (and `lineage.py`). New code that sets it elsewhere,
  reads a date before it is set, or introduces threads around QuantLib objects → wrong
  valuation date. Parallelism must stay process-level (`--workers`).
- `curve_cache.cached(key, build)`: a key missing any input that changes the curve
  (valuation date, snapshot/market inputs, bump/shift, currency, tenor grid, config) →
  a stale curve is reused, e.g. across bumped risk runs → wrong Greeks/VaR, silently.
- Bumps via quotes/handles: bumped quote not restored, or a copied value instead of a
  linked `Handle`/`SimpleQuote` so the bump never propagates (Greek = 0).

**Conventions (check against the trade/instrument definition and `docs/pricing/`)**
- Day counter, calendar, business-day convention, settlement days, end-of-month,
  compounding + frequency, fixing lag. Hardcoded vs trade-declared.
- **Units:** rates decimal vs percent vs bp; spreads bp vs decimal; vol decimal vs
  percent; **normal (bp) vs lognormal** vol (MOVE is normal); notional scaling; FX quote
  direction (USD/TWD vs TWD/USD), per-unit vs per-notional prices.
- **Signs:** long/short, payer/receiver, protection buyer/seller, P&L = new − base,
  VaR/ES reported as **positive loss** (`var.py`: loss quantile), component VaR/ES
  summing to total.

**Risk statistics**
- Quantile level vs tail (`confidence_levels` 0.95/0.975/0.99 → loss quantile at that
  level), ES = mean loss **beyond** VaR, horizon √h scaling, 252-day annualization,
  window alignment/dates, backtest exception counting (Kupiec/Christoffersen), covariance
  stays PSD after shrinkage (Ledoit-Wolf), factor mapping of new names (own factor vs proxy).

**Scenario math**
- Clamp/bound constants are model parameters: news `[0.8,1.5]`, geopolitical and
  throughput `[0.85,1.4]`, crowding `[0.8,1.4]`, `RETURN_FLOOR=-0.95`. A changed bound
  without a design-doc note is a model change.
- Composition order `idiosyncratic = ARag × news × crowding`; geo × throughput →
  `severity_multiplier`; liquidity scaling applies to **losses only**.
- **Point-in-time:** providers resolve to the run's `as_of`; any read of data after
  `as_of` is look-ahead bias.

**Governance**
- Pricing/curve/model code or `portfolio*.json` changed but golden PVs not re-run (or
  not runnable) → state it. `golden_pvs.json` edited without a `rebaseline_history`
  entry, or baseline date moved without `test_governance.py::VDATE` → finding.
- Float equality on computed values; tolerances in tests or `rel_tol`/`abs_tol` loosened.

## Severity anchors
- **P0:** wrong PV/Greek/VaR/ES/capital produced with no error on the default book
  (sign flip, unit error, stale cache, wrong eval date, look-ahead); golden PVs re-marked
  to hide an unexplained move.
- **P1:** convention error on a subset of trades; clamp/bound changed silently;
  governance lockstep missed; component risk no longer additive.
- **P2:** edge-date issue (holiday/EOM/leap) on a rare path; numerical instability only
  at extreme inputs; missing test for a new pricer branch.

## Proof recipes (scratch scripts, py312, PYTHONPATH = repo root)
- **Invariants** on a minimal instrument built from the changed code: European put-call
  parity `C − P = S·e^{−qT} − K·e^{−rT}`; discount factors in (0, 1] and non-increasing;
  zero-vol option → discounted forward intrinsic; par swap / par CDS PV ≈ 0; FX forward
  obeys covered interest parity; bump-and-reprice delta sign matches the position.
- **Cache key:** price → bump → reprice inside one `with curve_cache():` and assert the
  bumped PV differs.
- **Risk additivity:** component VaR/ES sum vs total on the scoped test fixtures.
- **Governance:** `python -m src.pricer.governance_runner` (needs `data/marketdata/`).
- Scoped tests: `pytest tests/pricer -q` / `tests/scenariogen -q`; check `$LASTEXITCODE`.

## Not findings
- Documented modeling assumptions (see profile Exclusions). Differences below the golden
  tolerance (`rel_tol 1e-6`, `abs_tol 0.01`). Proxy-factor choices recorded in the
  rebalance review doc.
