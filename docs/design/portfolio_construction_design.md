# Portfolio Construction: Risk-Appetite Position Sizing

How the 38-trade AI book is re-sized from flat ~$50mm placeholders into four
mandate-driven books — **Balanced**, **Core (Aggressive-Balanced)**, **Aggressive Growth**, and
**Highly Leveraged / Risky** — where every position size has a defensible rationale. **`core` is
the active default** since the 2026-07-26 rebalance (see
[`portfolio_rebalance_review.md`](portfolio_rebalance_review.md)).

Date: 2026-07-10 · Last updated: 2026-10-11 (§5 re-run on October data; §8 industry comparison added)
Last run: **2026-10-11, valuation date 2026-10-06** (run `6f2dc4d4`, after the 2026-10-06 book roll).
§5 reports this run, and it is **adopted**: every `portfolio_*.json` book and `portfolio.json` carry
these sizes (`sizing_basis.sizing_valuation_date` = 2026-10-06), and the golden PVs are rebaselined to
them (`configs/pricer/golden_pvs.json`, 2026-10-11 entry).
Module: [`src/pricer/sizing/`](../../src/pricer/sizing) · Config:
[`configs/pricer/sizing.json`](../../configs/pricer/sizing.json) · Runner:
`python -m src.pricer.sizing.sizing_runner`

---

## 1. The problem

The book in [`portfolio.json`](../../src/pricer/portfolio/portfolio.json) is sized to a
flat **~$50mm gross per trade** — the file's own `assumptions` block says these are
"REPRESENTATIVE placeholders." There is no reason NVDA is 240,000 shares or the 10Y payer
swap is $50mm notional; the sizes carry no view on risk, correlation, or return.

Portfolio construction replaces that with a repeatable process: pick a **risk-appetite
mandate**, and let the mandate — through a transparent optimizer — determine the size of
every position. The same 38 trades, four different books.

## 2. What a "mandate" is

A mandate is expressed along five axes. Each risk profile sets them differently:

| Axis | Balanced | Core (default) | Aggressive Growth | Highly Leveraged / Risky |
|---|---|---|---|---|
| **Risk budget** — 1-day 99% VaR as % of capital | 1.0% | 2.0% | 2.5% | 5.0% |
| **Gross leverage cap** — gross notional / capital | 3.0× (guardrail) | 3.5× | 5.0× | 10.0× |
| **Risk allocation** — how the risk budget is shared | mild return tilt over equal-risk | moderate Sharpe tilt | Sharpe tilt | concentrated Sharpe tilt |
| **Concentration** — max single-trade share of *directional budget* / notional | 15% / 10% | 15% / 12% | 25% / 20% | 45% / 35% |
| **Realized risk-share cap** — max single name's *realized net-book* RC share (`risk_share_cap`) | — | 18% | — | — |
| **Hedges** — fraction of the current hedge book carried | 100% | 85% | 50% | 15% |

The **capital base** (a fixed NAV, $1bn placeholder) and the **VaR budgets** are the two
primary dials. Both live in `sizing.json` and should be set to the desk's real NAV and
mandate at go-live. Everything else follows from the risk engine, which is calibrated to
market data.

## 3. Methodology

The construction is deliberately **transparent and staged** so every size is explainable,
rather than a black-box optimizer output. Five steps.

### 3.1 Trade-level risk model — [`trade_risk.py`](../../src/pricer/sizing/trade_risk.py)

Portfolio construction needs a covariance between *trades*, not just risk factors. We
build it by reusing the desk's own VaR machinery:

1. Compute each trade's first-order sensitivities (DV01/key-rate, CS01, equity delta &
   vega per underlier, FX delta) via `RiskEngine.sensitivities` — the same Greeks the
   production risk report uses.
2. Project each trade's sensitivity vector onto the **aligned historical factor-move
   panel** the VaR engine already builds (250 trading days × ~40 factors). This yields a
   **per-trade hypothetical daily P&L series** — exactly `VarEngine`'s calculation, but
   kept per-trade instead of summed to book level.
3. Stack those series into a P&L matrix **P** (250 × 38). Its sample covariance is the
   empirically-diversified **38×38 trade covariance Σ**.

Because first-order P&L is linear in position size, resizing trade *j* by a multiplier
*mⱼ* simply scales column *j*. The resized book's daily P&L vector is **P·m**, and its
VaR/ES are quantiles of **−(P·m)**. This gives us portfolio risk for *any* resizing in
one matrix multiply.

> **Consistency check.** At the current sizes (m = 1, the base placeholder book) the summed
> per-trade P&L equals the production VaR engine's book P&L **to the dollar** — 99% VaR
> $13.9mm, ES 97.5% $14.7mm at 2026-10-06 ($15.2mm / $15.5mm at 2026-07-10). The sizing model is therefore consistent with the desk's
> reported risk, not a parallel universe. This is asserted in `tests/pricer/test_sizing.py`.

### 3.2 Expected returns — hybrid — [`expected_returns.py`](../../src/pricer/sizing/expected_returns.py)

The "growth" in Aggressive Growth needs a return signal. We use a **hybrid** model —
carry/theta where the economics are observable, an explicit view where they are not:

| Sleeve | Expected annual return (at current size) |
|---|---|
| Cash equity | market value × (β·ERP + growth tilt) |
| Corp / UST bonds | (coupon − funding) × face |
| IRS (payer) | (floating − fixed) × notional ≈ (funding − fixed) × notional |
| Swaptions / cap | −PV / max(T, 1) — long-premium theta, capped at the premium |
| Equity options | δ·(underlier drift)·100 − PV/max(T,1) — the delta view **plus** theta decay |
| CDS sell / buy | ± running coupon × notional (carry earned / protection paid) |
| FX forward | (funding − foreign rate) × usd_notional |
| XCCY swap | (usd fixed − foreign fixed) × usd_notional |

Signs follow the book's P&L: a short's expected return is negative, a bought hedge's carry
is a cost. Every coefficient (ERP 5.5%, per-name betas, growth tilts, 4.3% funding,
foreign rates) is config-driven and auditable. **These are assumptions** — they shape the
aggressive/risky tilt only; they do not touch the risk (VaR/Σ) calibration.

*Note:* the option delta term is why a bullish call is held at all — pure theta would make
every long option look like a guaranteed loss. The θ proxy overstates decay for deep
in-the-money options (PV carries intrinsic, which does not decay); material only for the
AAPL LEAPS, and flagged in the model output.

### 3.3 Risk allocation — [`optimizer.py`](../../src/pricer/sizing/optimizer.py)

We split the book into **directional** trades and **hedges**, and allocate the directional
risk budget by target *component risk contributions* `b`:

- **Equal risk contribution (risk parity)** — every directional trade contributes the same
  risk. Return-agnostic; the diversified default.
- **Return tilt** — `bⱼ ∝ max(Sharpeⱼ, 0)^power + floor`, so risk concentrates in the
  highest risk-adjusted-return names. `power` grows with appetite (0.5 balanced → 1.0
  aggressive → 2.0 risky). A concentration cap limits any single trade's share of the
  *directional budget* here; a separate **realized** risk-share cap (§3.5) bounds its share
  of the *net-book* risk after hedging.

Given budgets `b`, directional multipliers are solved by the standard convex risk-budgeting
program `min ½·xᵀΣx − Σ bᵢ·ln xᵢ` (Spinu), whose optimum has `RCᵢ ∝ bᵢ`. Solved by cyclical
coordinate descent; multipliers are normalised to mean 1.

### 3.4 Hedge treatment

Hedges are held at **hedge_scale × their current size** (absolute), *not* scaled with the
directional book. This is a deliberate design choice: if a hedge were scaled up with the
book while risk-parity shrinks the specific positions it offsets, the "hedge" would flip
into a dominant directional short. Holding hedges at appetite × current size means the
balanced book carries full downside protection, while the risky book — scaled far up on the
directional side — naturally has hedges shrink to a thin overlay (further cut to 15%).

**SMH-short is treated as directional**, not a hedge: it is the short leg of the semis
relative-value structure (long single-name semis / short the SMH basket), so it is sized
with the directional book and correctly shows a *negative* risk contribution (it reduces
book risk) rather than being pinned to a hedge ratio.

### 3.5 Scaling to the VaR budget

With directional shape and hedges fixed, the whole book is scaled by one scalar **S** so
its 1-day 99% VaR meets the mandate's budget. Because VaR is positively homogeneous, this
is a clean 1-D solve (bisection on `VaR(S·directional + hedge)`). Five further steps then
apply, in order, and **the last one that binds is reported**:

1. **Per-trade notional cap** — no single trade's gross notional may exceed
   `notional_cap_pct` of capital. Prevents an ultra-low-vol carry trade (a $50mm CDX index)
   from being levered to absurd size just to absorb risk budget. Because this cap can trim a
   risk-*reducing* leg (an offsetting short) and push VaR back above budget, a **scale-to-budget**
   re-scale then returns the book to its VaR budget.
2. **Gross-leverage guardrail** — if the capped book still exceeds the leverage cap, the
   whole book is scaled down uniformly (VaR then lands below budget).
3. **Market-neutral pair pins** (`market_neutral_pairs`) — for a configured long/short pair,
   the short leg's notional is pinned to `short_ratio` × the long's (beta-neutral when
   `short_ratio` = β_long/β_short), overriding the return-tilt that would otherwise floor the
   negative-carry short.
4. **Realized single-name risk-share cap** (`risk_share_cap`) — the concentration cap
   in §3.3 bounds only the *intended directional* risk budget, so the top single name can still
   dominate the *realized net-book* component-VaR share (MU ~24% at a 15% directional budget). This
   step flattens any directional name whose realized share `RC_j / vol` exceeds the cap, then
   re-hits the VaR budget. Realized RC share is invariant to a uniform rescale, so the re-scale
   preserves both the cap and any pair's leg ratio. Off by
   default (1.0); `core` = 0.18.
5. **Final leverage guardrail** (runs last) — step 4's re-scale to the VaR budget would inflate a book
   that the leverage cap had held below budget back past the cap, so the cap is re-applied last
   (a uniform scale-down, which keeps realized risk shares, pair leg ratios and the notional cap
   intact). Whenever it bites, VaR lands below budget and `leverage_cap` is reported
   (`sizing-leverage-cap`, 2026-10-11).

## 4. Re-valuation: PV, Risk, Capital per configuration

Once each profile's multipliers `m` are solved, we re-value the book under each
configuration. Position scaling is **exactly linear** for a fixed market state (m identical
contracts = m× the P&L, including gamma), so we scale per-trade quantities rather than
re-price from scratch:

- **PV** — `Σ mⱼ · PVⱼ` (book value of the sized book).
- **Risk** — 99% VaR and 97.5% ES from `−(P·m)`; plus each configured **stress scenario's**
  P&L as `Σ mⱼ · pnlⱼ(scenario)` from the per-trade `ScenarioEngine` results.
- **Capital** — two lenses: **economic capital** = 97.5% ES (the FRTB tail measure), and
  **stress capital** = the worst loss across the standard stress scenarios
  (rates sell-off, credit blow-out, Taiwan, AI-capex bust/boom).

This lets a user compare not just risk but the full P&L / capital footprint of each mandate
on identical instruments.

## 5. Results (valuation date 2026-10-06, run 2026-10-11; $1bn capital; 38-trade book after the 10-06 roll)

Run `6f2dc4d4` sized the rolled base book on 2026-10-06 market data: 250 aligned days
(the factor panel ends 2026-10-06, so there is no look-ahead), 40 factors, 38 trades. The previous
(2026-07-10) figures are in brackets.

| | Balanced | **Core (default)** | Aggressive | Risky |
|---|--:|--:|--:|--:|
| 1-day 99% VaR | $9.6mm (1.0%) [$9.6mm] | **$20.0mm (2.0%)** [$20.0mm] | $24.4mm (2.4%) [$24.2mm] | $49.8mm (5.0%) [$49.8mm] |
| ES 97.5% | $10.0mm | **$21.2mm** | $25.2mm | $51.8mm |
| Gross leverage | 1.85× [1.52×] | **2.80×** [2.18×] | 3.22× [2.58×] | 6.26× [5.29×] |
| Hedge book | full | **85%** | 50% | 15% |
| Expected return | +1.6% [+1.2%] | **+3.8%** [+3.0%] | +4.8% [+4.2%] | +10.2% [+8.6%] |
| Return / VaR | +1.61 [+1.20] | **+1.90** [+1.49] | +1.93 [+1.67] | +2.04 [+1.72] |
| Worst-stress loss | −$61.0mm [−$64.7mm] | **−$167.6mm** [−$156.6mm] | −$211.2mm [−$202.7mm] | −$454.6mm [−$419.1mm] |

Every profile's worst stress is still AI-capex-bust. The unsized base book (m = 1) is at 99% VaR
$13.9mm and a −$95.7mm capex-bust loss.

**What changed since July.** Every profile now needs ~18–28% more gross notional to fill the same VaR
budget. Each dollar of notional carries less VaR in the trailing 250-day window: the unsized base book's
VaR fell from $15.2mm to $13.9mm.
More gross for the same VaR raises the expected return and return/VaR, but stress losses (which do not
shrink with realised vol) grow in the core, aggressive and risky books — core's worst stress rises from
15.7% to 16.8% of NAV. That is gap 7 in §8.3 in practice: the VaR budget is binding, the stress tail is
not. The SMH short (TRADE_6) is the largest risk *reducer* in every book (−26% to −37% of risk). The top
risk takers are MU, NVDA, TSM and XLK (TRADE_4, 1, 2, 5). In core every eligible name sits at the 18%
`risk_share_cap`. The INTC pair leg (TRADE_37) is at 22%, because pair legs are excluded from the cap by
design (§3.5).

> **Adopted 2026-10-11.** `sizing_runner` wrote all four book variants and `portfolio.json` (= core),
> replacing the July sizes (the five trades rolled on 10-06 had kept their July multipliers until
> then). Pricing is unchanged: each trade's PV moved exactly by its position-size ratio (option trades by
> their integer contract-count ratio), checked trade by trade before the golden PVs and the per-asset
> golden pins were rebaselined. Book PV $1,107.2mm → $1,393.4mm. The downstream results (PV, risk,
> scenarios, governance, lineage) and the per-trade pricing pages were regenerated at 2026-10-06 on the
> new book.

Reading across: risk-adjusted return climbs as the mandate loosens. **`core` is the active default** —
it sits between balanced and aggressive: it keeps the growth tilt but restores most hedges (85%), caps
single-name notionals at 12% of NAV, and — new since 2026-07-27 — caps each single name's *realized*
net-book risk share at 18% (`risk_share_cap`, §3.5), so no one name dominates and every stress tail is
~25% smaller than the aggressive book's (see
[`portfolio_rebalance_review.md`](portfolio_rebalance_review.md)). The **aggressive** and **risky** books
thin the hedges and concentrate risk into the top AI names (NVDA, MU, TSM, AVGO, XLK) with the SMH short
offsetting; the **balanced** book stays diversified with full downside hedges (notional caps bind before
its VaR budget — it is limit-constrained, not risk-hungry). All four are on the **corrected ~4% SOFR
curve** and the full 38-trade book (Part 2 diversifiers + the N5 dispersion pair + the realized
risk-share cap — see the rebalance review); the table was last refreshed by the 2026-10-11 run above.

## 6. Assumptions & limitations

- **Two dials are placeholders**: capital base ($1bn) and VaR budgets (1/2.5/5%). Set to the
  desk's real NAV and mandate in `sizing.json`.
- **Expected returns are assumption-driven** and shape only the tilt; the risk engine is
  market-calibrated.
- **First-order risk**: sizing uses sensitivity-based P&L (matching the desk VaR). Option
  gamma/convexity is not re-optimized (though it *does* scale linearly with contract count
  in the re-valuation).
- **θ proxy** caps option decay at the premium and overstates decay for deep-ITM options.
- Leverage is measured on gross notional; option underlying notional is conservative
  (overstates true capital-at-risk).
- **Curve (2026-07-11, resolved).** Sizing is now computed on the corrected **~4% SOFR curve**.
  An earlier run used a degenerate ~0% curve (the Pensford file held a single forward point per
  snapshot), which understated the rates/bonds risk; it has been re-sized on the fixed curve. See
  [`01-sofr-multicurve.md`](../pricing/methodology/01-sofr-multicurve.md) and
  `scripts/rebuild_pensford_from_supabase.py`.

## 6a. Book variants (pricing the book as a variable)

The sizing runner keeps the book **selectable**: the canonical **base** book
(`portfolio_base.json`, the ~$50mm placeholder) is sized into four variants —
`portfolio_{balanced,core,aggressive,risky}.json` — and the configured `default_profile` (**core**)
is copied to `portfolio.json`, the **active default** every runner prices. Pick a book at pricing
time with `--book base|balanced|core|aggressive|risky` (or the `PRICER_BOOK` env var; the scenariogen
runner also takes `--book`); sizing always sizes the *base* book so re-running never compounds. Code:
[`src/pricer/sizing/book_variants.py`](../../src/pricer/sizing/book_variants.py),
`resolve_book_path` in
[`portfolio_loader.py`](../../src/pricer/portfolio/portfolio_loader.py).

## 7. Reproduce

```
python -m src.pricer.sizing.sizing_runner            # size base -> write all 4 variants + default
python -m src.pricer.runner  --book balanced         # price a specific book
python -m src.pricer.risk_runner --book risky        # risk a specific book
```

Writes `data/pricer/sizing/<date>/sizing_{latest,<run_id>}.json`. Surfaced in the UI under
the **Portfolio Construction** subsystem (BFF `/api/sizing/*`).

## 8. Industry context: what portfolio construction is, and how this module compares

*Added 2026-10-11.* This section places the module in the wider practice: what a portfolio construction
(PC) function does in an investment firm, how firms usually build it, and where `src/pricer/sizing`
matches or falls short of that. It is a review, not a plan; §8.4 lists candidate follow-ups, each of
which would go through the test-first `spec/<id>` → `impl/<id>` flow.

### 8.1 What the PC function does

PC sits between research and trading:

```
Research / alpha ─► PORTFOLIO CONSTRUCTION ─► Trading / execution ─► Risk monitoring ─► Attribution
 (what is attractive)  (how much of each, given      (getting fills        (inside limits?)    (why did it
                        risk, constraints, costs)     cheaply)                                   pay or not?)
        ▲                                                                                            │
        └──────────────────────────────── feedback loop ─────────────────────────────────────────────┘
```

Research decides **what** is attractive. PC decides **how much** to hold, given the risk budget, the
mandate's constraints, trading costs and the current holdings. Its output is target weights or
positions, which become orders. Its responsibilities are:

1. **Turning the mandate into constraints.** The client's investment policy statement, the fund
   prospectus or the regulator sets the rules: benchmark, tracking-error budget, leverage, long-only or
   not, sector and country bands, issuer limits (UCITS 5/10/40, 1940-Act diversification), ESG
   exclusions and liquidity. PC encodes these as optimizer constraints and pre-trade compliance rules.
2. **Asset allocation.** Strategic allocation (the long-run policy mix, often set with mean–variance or
   asset–liability studies) and tactical tilts away from it.
3. **Turning views into weights.** Combining alpha signals or PM conviction with a risk model.
4. **Risk budgeting.** Splitting total risk (volatility, tracking error, VaR) across asset classes,
   strategies, PMs, factors and single names, and neutralising unintended bets such as hidden
   momentum or rate exposure.
5. **Rebalancing and implementation.** When to trade (calendar, threshold or signal trigger) and how far
   toward the target, trading off drift against turnover and cost; overlays such as FX hedges, futures
   beta overlays and cash equitisation.
6. **Liquidity and capacity.** Sizing relative to average daily volume (ADV), days-to-liquidate and the
   fund's redemption terms.
7. **What-if and pro-forma analysis.** "If I add X, what happens to risk, exposures and stress P&L?"
8. **Governance.** Investment-committee sign-off, model validation (SR 11-7 in banks) and an audit
   trail of why each position has its size.

What PC means differs by firm:

| Firm type | What PC means there | Typical risk unit |
|---|---|---|
| Long-only asset manager | Benchmark-relative weights; active bets inside a tracking-error budget | Tracking error (1–6%) |
| Pension / endowment / OCIO | Strategic and tactical allocation, liability-driven investing, manager selection | Surplus volatility, funded ratio |
| Multi-manager hedge fund | A central team allocates capital and risk to pods, nets exposures across pods and runs a central hedge book; PMs size their own ideas inside hard limits | Vol or VaR per pod; drawdown stops (e.g. −5% halves capital, −7.5% stops the pod); factor-neutrality rules |
| Systematic quant | The optimizer is the core: signals → alpha → daily optimization with a cost model | Ex-ante vol, factor exposures |
| Wealth / model portfolios | Model portfolios, direct indexing, tax-loss harvesting | Tracking error to the model, after-tax return |
| Sell-side trading desk | Called limit allocation rather than PC: VaR, DV01, CS01 and stress limits across desks and books | VaR, stress, regulatory capital |

**Faultline is closest to a mix of a multi-strategy pod and a trading desk.** A human picks the trade
ideas and their direction; the system sizes them against a VaR mandate. The comparison in §8.3 is made
against that kind of firm.

### 8.2 How firms usually build it

**The stack.** Market and reference data → risk model (MSCI Barra, SimCorp Axioma, Bloomberg PORT,
Northfield, or in-house) → expected returns (signals, analyst views, Black–Litterman blend) → optimizer
(Axioma, Barra Optimizer, or in-house on MOSEK / Gurobi / CVXPY) → pre-trade compliance (Charles River,
Aladdin, SimCorp) → order and execution management → transaction-cost analysis (TCA) → attribution
(Brinson for allocation decisions, factor-based for risk-model portfolios). BlackRock Aladdin is the
well-known example of all of these in one platform.

**The risk model.**

- **Factor models are the default:** Σ = X F Xᵀ + D, with exposures X, factor covariance F and specific
  variance D. This avoids estimating N² parameters from short histories and lets exposures be
  constrained directly ("beta-neutral", "DV01 ≤ x").
- **Estimation hygiene:** exponentially weighted (EWMA) or volatility-regime-adjusted estimates,
  Ledoit–Wolf shrinkage, Newey–West for autocorrelation, and an eigenfactor adjustment because
  optimizers seek out the directions where risk is underestimated.
- **Option books:** delta-gamma or full revaluation over scenarios, because a linear Σ understates
  convexity.

**Common methods**, roughly in order of how often they are used:

| Method | Idea | Typical users |
|---|---|---|
| Constrained mean–variance | max αᵀw − λ·wᵀΣw − TC(w − w₀) subject to constraints | Most quant equity and active managers |
| Black–Litterman | Start from equilibrium returns and blend in views weighted by confidence; gives stable inputs | Strategic/tactical and multi-asset |
| Risk parity / risk budgeting | Set each position's risk contribution to a target (Spinu or Roncalli solvers) | Multi-asset, alternative risk premia |
| Minimum variance, maximum diversification, hierarchical risk parity | Avoid estimating returns; HRP clusters instead of inverting Σ | Defensive and smart-beta products |
| Volatility targeting | Scale the whole book so ex-ante vol stays at a target | Almost everyone, as an overlay |
| Fractional Kelly / Grinold–Kahn | Size ∝ conviction × information coefficient ÷ risk | Discretionary and quant sizing rules |
| CVaR / ES optimization | Rockafellar–Uryasev linear program over scenarios | Tail-aware and option-heavy books |
| Robust / resampled mean–variance | Explicit handling of estimation error | Institutional allocation |
| Multi-period, cost-aware (Gârleanu–Pedersen) | Trade gradually toward an "aim" portfolio | High-turnover quant |

**Constraints a production optimizer carries:** gross and net leverage; position bounds per name,
sector and country; factor-exposure bands (beta, size, value, momentum, DV01, CS01); a tracking-error
cap; a turnover cap; liquidity (at most k% of ADV, days-to-liquidate); cardinality (minimum and maximum
number of names, which needs mixed-integer programming); minimum trade size; ESG rules; and **stress-loss
limits as hard constraints** (for example "loss in the 2008 replay ≤ 8% of NAV"). All of them go into
**one** convex or mixed-integer problem, so the solver trades them off jointly and reports which bind,
with their shadow prices.

**The operating loop** runs daily or intraday: mark current holdings → refresh the risk model and
alphas → optimize **from current holdings** with turnover penalised → pre-trade compliance → orders →
TCA and attribution, which feed back into the alphas and the cost model.

### 8.3 How `src/pricer/sizing` compares

**Where it matches or exceeds practice.**

| Aspect | Assessment |
|---|---|
| The sizing risk model is the reported risk model | **Ahead of common practice.** The trade-level Σ comes from the same sensitivities × factor moves as the desk VaR and reconciles to it at m = 1 (§3.1). In many firms the optimizer's vendor risk and the risk team's VaR disagree, and PMs exploit the gap. |
| Risk budgeting (§3.3) | Textbook: the Spinu log-barrier program is convex with a unique solution, and is what risk-parity managers run. |
| Euler risk contributions and the realized risk-share cap (§3.5) | Standard decomposition. Separating intended (directional-budget) concentration from realized (net-book) concentration matches how risk teams measure it. |
| Mandate as a named, config-driven profile (§2) | Matches how real mandates are written: VaR budget, gross cap, single-name cap, hedge ratio. |
| Scaling to a VaR budget | The vol-targeting overlay everyone runs; valid because VaR is positively homogeneous. |
| ES and stress per profile (§4) | Matches the pro-forma "what does this book lose in scenario X" view risk committees ask for. |
| Beta-neutral pair pin | How pods size relative-value structures: as one unit with a neutrality ratio. |
| Provenance | At or above practice: run id, git SHA and `sizing_basis` are stamped into every book. |
| Humans choose direction; the system sizes | Right for a discretionary or pod-style book. Multipliers never flip a sign. |

**Gaps, roughly by importance.**

1. **Sequential steps instead of one joint optimization.** Each step in §3.5 can undo an earlier one,
   and `binding_constraint` reports only the last step that fired. One concrete case, **reproduced
   2026-10-11 with a synthetic 4-trade book**: when the gross-leverage cap binds (step 5 scales the book
   down, leaving VaR below budget) and the risk-share cap then trims a name, the final
   `scale_to_var_budget` call ([`optimizer.py`](../../src/pricer/sizing/optimizer.py), step 7) scales
   the book back **up** to the VaR budget without re-checking gross. The probe ended at **2.18× gross
   against a 0.5× cap**, reporting `binding = risk_share_cap`. *Fixed by `sizing-leverage-cap`
   (2026-10-11):* a final leverage guardrail (§3.5 step 5) now re-applies the cap after every other step;
   no committed book changed, since none was near its cap. The reported binding constraint is also
   unreliable: in the 2026-10-06 run, balanced, aggressive and risky all report `market_neutral_pair`,
   which is simply the last step that ran, not the limit that actually set their size. Industry would solve
   one problem (for example in CVXPY): maximize μᵀm − λ·risk subject to VaR/ES ≤ budget, gross ≤ cap,
   |notional| ≤ cap, risk share ≤ cap, pair equalities and stress ≤ limit, which also yields every
   binding constraint and its shadow price.
2. **Return signals are thin and uncalibrated.** μ (§3.2) is a set of assumptions: config betas, a flat
   5.5% ERP and hand-set growth tilts. Firms derive μ from signals with a measured information
   coefficient, or shrink it toward equilibrium (Black–Litterman) weighted by confidence. The tilt also
   uses **standalone** Sharpe (μ ÷ the trade's own vol), whereas mean–variance uses **marginal** Sharpe
   (Σ⁻¹μ), which gives less weight to a high-Sharpe trade that is highly correlated with the rest of the
   book. Sharpe^p as a risk budget is a reasonable heuristic but not a utility-optimal rule.
3. **No transaction costs, turnover or liquidity.** Sizing always starts again from the base book
   (§6a) rather than from current holdings, so re-sizing has no turnover or market-impact cost
   (square-root or Almgren–Chriss models). There is no ADV or days-to-liquidate limit;
   `notional_cap_pct` stands in for one but ignores how liquid each instrument is — $120mm of NVDA is
   very different from $120mm of a single-name CDS.
4. **Risk-model estimation.** Σ is an equal-weighted sample covariance over 250 days, with no EWMA,
   shrinkage or stressed blend. The risk-budget solve and the risk-share cap use **volatility** Euler
   contributions while the budget is **99% historical VaR**; for fat-tailed or option positions the
   two rank trades differently. A 99% quantile from 250 observations is about the 2nd–3rd worst day,
   so it is noisy. The risk engine already has filtered historical simulation, stressed VaR and
   component ES; sizing does not use them.
5. **First-order risk in sizing** (also §6). Option convexity is not in Σ, which matters for this book
   (LEAPS, the SPX convexity put, swaptions, the cap). Desks with option books usually size on
   delta-gamma or full-revaluation scenario P&L.
6. **Hedges are a fixed ratio, not optimized** (§3.4). `hedge_scale` multiplies every hedge uniformly.
   Firms solve hedge sizes to neutralise target exposures (beta, DV01, CS01, FX) or to minimise
   residual risk per unit of hedge cost. Apart from the one pair pin there are no factor-exposure bands
   such as "net equity beta ≤ x" or "DV01 within ±y".
7. **Stress results are reported but do not constrain sizing.** Core's worst stress loss in §5 is
   −$167.6mm, 16.8% of NAV, against a 2% daily VaR budget, and nothing limits it. It *rose* from 15.7%
   in July even though the VaR budget did not change, because calmer realised vol let the VaR budget
   buy more notional. Most institutional mandates and multi-strategy risk teams set stress-loss limits
   that bind alongside VaR.
8. **Static and single-period.** There are no rebalance triggers, no drawdown-based de-risking and no
   volatility-regime scaling over time. In production PC runs as a loop; here it is a one-off batch.
   The 2026-10-06 roll shows the effect: five trades were rolled at their July multipliers, and the
   committed books were not re-sized until the manual, one-off re-size of 2026-10-11 (§5).
9. **Leverage and financing.** Gross leverage is measured on notional (§6), which overstates capital at
   risk for options. Firms measure balance-sheet use through margin (Reg T or portfolio margin, SIMM for
   OTC, CCP initial margin) and funding cost; the carry model here uses one funding rate for everything.
10. **No execution chain.** There is no pre-trade compliance engine, order management or TCA; the output
    is a JSON book. That fits this platform's scope but is the largest structural difference from a
    firm's PC stack.

**Scorecard.**

| Dimension | Industry practice | This module | Gap |
|---|---|---|---|
| Risk model consistent with reported VaR | Often inconsistent | Reconciles exactly | **Ahead** |
| Risk-budgeting math | Spinu / Roncalli | Spinu coordinate descent | On par |
| Joint constrained optimization | Single QP / SOCP | Sequential steps | Medium–large |
| Expected returns | Calibrated signals, Black–Litterman | Config assumptions | Large (by design) |
| Covariance estimation | Factor model + shrinkage + EWMA | Sample covariance of factor-projected P&L | Medium |
| Non-linear risk in sizing | Delta-gamma / full revaluation | First-order | Medium |
| Transaction costs, turnover, liquidity | Core inputs | Absent | Large |
| Factor-exposure and stress constraints | Hard constraints | Reported only | Medium |
| Dynamic rebalancing / drawdown control | Standard | Absent | Medium |
| Provenance and audit | Varies | Strong | **Ahead** |

### 8.4 Candidate follow-ups, in priority order

1. **Replace the post-solve steps of §3.5 with one CVXPY problem**, keeping the Spinu risk-budget result
   as a warm start or soft target. This fixes the constraint interaction in gap 1 and gives shadow
   prices.
2. **Add stress-loss limits and factor-exposure bands as constraints.** The per-trade scenario P&L
   matrix (§4) already exists.
3. **Size from current holdings with a turnover penalty and an ADV limit**, so sizing becomes a
   rebalancing tool rather than a from-scratch rebuild.
4. **Budget in ES or filtered historical simulation** from the risk engine, and decompose risk in the
   same measure.
5. **Shrink μ toward equilibrium** (a light Black–Litterman) and replace standalone with marginal Sharpe.
