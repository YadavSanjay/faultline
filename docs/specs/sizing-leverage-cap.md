# sizing-leverage-cap — the sizing optimizer must not breach the gross-leverage cap

## Requirement

`src.pricer.sizing.optimizer.solve_profile` sizes a book in steps (see
`docs/design/portfolio_construction_design.md` §3.5): scale the directional book to the VaR budget,
apply the per-trade notional cap, apply the **gross-leverage cap** (step 5: if gross notional exceeds
`gross_leverage_cap × capital_base`, scale the whole book down uniformly, leaving VaR below budget),
pin market-neutral pairs, and finally apply the **realized risk-share cap** (step 7: trim any eligible
directional name whose realized share of portfolio volatility exceeds `risk_share_cap`, then call
`TradeRiskModel.scale_to_var_budget` to return the book to its VaR budget).

The defect: when step 5 has bound (VaR is below budget because leverage, not VaR, limits the book)
and step 7 then trims a name, step 7's re-scale to the VaR budget scales the book back **up** and
never re-checks gross notional. The result breaches the leverage cap it was given, and reports
`binding_constraint = "risk_share_cap"`. A synthetic 4-trade book reproduces it: a 0.5× leverage cap
ends at about 2.18× gross.

When this change is done:

1. **Both caps hold together.** Whenever `0 < risk_share_cap < 1`, the returned book's gross leverage
   (Σ |target notional| / capital base) is at most `gross_leverage_cap`, and every eligible
   directional trade's realized risk share is still at most `risk_share_cap` (the optimizer's own
   tolerance, 1e-4). Satisfying one cap by dropping the other is not a fix.
2. **The binding constraint is reported truthfully in this case.** When the leverage cap is what
   limits the final book (its gross leverage equals the cap and its 1-day 99% VaR is below the VaR
   budget), `SizingResult.binding_constraint` is `"leverage_cap"` and `achieved_var99` is below
   `var_budget`.

Out of scope (separate issues, not to be changed here): the step-7 notional-cap clip after the
re-scale, the market-neutral pair step overriding the reported binding constraint, and any change to
the sizing method itself. The implementer covers behaviour that is already correct and must stay
so — VaR lands exactly on budget when no cap binds; the risk-share cap still holds when the leverage
cap is slack — with unit tests in `tests/unit/`.

## Acceptance criteria

- **C1**: For any book and profile in which the leverage cap and the risk-share cap both bind, the
  returned book's gross leverage, recomputed from `SizingResult.sizes` as
  Σ |`target_notional`| / `capital_base`, is ≤ `gross_leverage_cap` within a relative tolerance of
  1e-9, **and** every directional, non-pair trade's `risk_share` is ≤ `risk_share_cap` + 1e-4.
  Oracle: the mandate definitions in `src/pricer/sizing/profiles.py` (gross_leverage_cap = max gross
  notional / capital base; risk_share_cap = max single directional name's realized net-book risk
  share) applied to a hand-built synthetic book; the risk-share tolerance is the optimizer's
  documented trim tolerance.
- **C2**: In those same cases, `SizingResult.binding_constraint == "leverage_cap"` and
  `achieved_var99 < var_budget`. Oracle: the meaning of a binding constraint — the leverage cap is
  binding when it, not the VaR budget, sets the book's scale; a book held below its VaR budget by the
  leverage cap has VaR strictly below budget.

## Interface

No new interface. The existing signatures are unchanged:

- `src.pricer.sizing.optimizer.solve_profile(model: TradeRiskModel, mu: dict[str, ExpectedReturn],
  profile: RiskProfile, capital_base: float, pairs: list[dict[str, Any]] | None = None) -> SizingResult`
- `src.pricer.sizing.trade_risk.TradeRiskModel(trades: list[TradeRisk], pnl_matrix: np.ndarray,
  factor_columns: list[str], dates: list[str])` and `TradeRisk(trade_id, asset_class, description, pv,
  notional, sensitivities, is_hedge=False, standalone_vol=0.0, standalone_var99=0.0)`
- `src.pricer.sizing.profiles.RiskProfile(key, label, var_budget_pct, gross_leverage_cap,
  concentration_cap, risk_share_cap, notional_cap_pct, hedge_scale, method, tilt_power, sharpe_floor,
  description="")`
- `src.pricer.sizing.expected_returns.ExpectedReturn(trade_id, mu_annual, method, detail="")`

## Notes

- **Synthetic data only.** Build the book directly with `TradeRiskModel(...)` from a P&L matrix made
  in the test (e.g. `numpy.random.default_rng(seed).standard_normal((250, n)) * vol`); never call
  `TradeRiskModel.build` (it reads `data/`). Set each `TradeRisk.standalone_vol` to the sample standard
  deviation (ddof=1) of its P&L column, as `build` does — the return tilt divides by it, and a zero
  makes every Sharpe zero and the tilt collapse to equal risk, so the risk-share cap never binds.
- **Recipe that makes both caps bind** (reproduces the defect; verified against the current code on
  2026-10-11): 4 directional trades (no hedges), 250 days, each column i.i.d. normal with daily vol
  $1mm (seed 0), notional $50mm each, capital base $1bn; one trade (`T0`) with `mu_annual` $50mm and
  the other three $1mm; profile `var_budget_pct=0.05`, `gross_leverage_cap=0.5`,
  `concentration_cap=1.0`, `risk_share_cap=0.30`, `notional_cap_pct=10.0` (slack), `hedge_scale=1.0`,
  `method="return_tilt"`, `tilt_power=2.0`, `sharpe_floor=0.01`, `pairs=None`. The current code returns
  gross ≈ 2.18× and `binding_constraint="risk_share_cap"`.
- Why both caps bind there: a $50mm VaR budget needs far more than $500mm gross, so the leverage cap
  binds at step 5; the tilt concentrates risk in `T0` well above 30%, so step 7 trims it.
- Keep the notional cap slack and `pairs=None` in every test case, so the out-of-scope interactions
  above cannot decide the outcome.
- `risk_share` in `SizingResult.sizes` is signed and is each trade's Euler share of portfolio
  volatility (`TradeRiskModel.risk_contributions / portfolio_vol`).
