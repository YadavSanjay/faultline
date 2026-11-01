"""Position-sizing optimizer.

Turns a profile mandate + trade-level risk model + expected-return vector into per-trade
size multipliers (scale factors on the current signed position) and the resulting book
metrics. Construction is deliberately transparent and two-staged so every size is
explainable:

1. **Directional risk allocation.** Split the book into directional trades and hedges.
   Over the directional sub-covariance, solve for size multipliers whose *component risk
   contributions* match a target budget ``b``:
     - ``erc``         → equal budget (risk parity): every directional trade contributes
                         the same risk. Return-agnostic — the balanced default.
     - ``return_tilt`` → budget prop max(Sharpe, 0)^power + floor, so risk concentrates in
                         the highest risk-adjusted-return names. A concentration cap limits
                         any one trade's share.
   The risk-budget solve is the standard convex program
   ``min 1/2 xTSigmax - Sigma bi ln xi`` (Spinu), solved by cyclical coordinate descent; its
   solution has RCi prop bi. Multipliers are normalised to mean 1 across directional trades.

2. **Hedges + scaling.** Hedges are set to ``hedge_scale`` x current size (full hedge in
   balanced → minimal in risky). The whole vector is then scaled by one scalar ``S`` so
   the book's 1-day 99% VaR equals the profile's budget (VaR is positively homogeneous, so
   ``S = target / VaR(base)``). The per-trade notional cap is applied next; because that cap
   can trim a risk-*reducing* leg (an offsetting short) and thereby push VaR back *above*
   budget, a final homogeneous re-scale returns the book to its VaR budget. If instead the
   gross-leverage cap binds, ``S`` is clipped to it and VaR lands below budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .expected_returns import ExpectedReturn
from .profiles import RiskProfile
from .stress_constraint import StressInput, stress_scale, worst_stress
from .trade_risk import TradeRiskModel

logger = logging.getLogger("pricer.sizing.optimizer")

_TRADING_DAYS = 252


@dataclass
class TradeSize:
    trade_id: str
    asset_class: str
    description: str
    is_hedge: bool
    current_multiplier: float           # always 1.0 (reference)
    multiplier: float                   # solved size vs current
    current_notional: float
    target_notional: float
    mu_annual: float                    # expected annual P&L at TARGET size
    risk_contribution: float            # component 1d VaR contribution ($) at target
    risk_share: float                   # fraction of portfolio risk
    standalone_var99: float             # at target size

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "asset_class": self.asset_class,
            "description": self.description,
            "is_hedge": self.is_hedge,
            "multiplier": self.multiplier,
            "current_notional": self.current_notional,
            "target_notional": self.target_notional,
            "mu_annual": self.mu_annual,
            "risk_contribution": self.risk_contribution,
            "risk_share": self.risk_share,
            "standalone_var99": self.standalone_var99,
        }


@dataclass
class SizingResult:
    profile: str
    label: str
    capital_base: float
    var_budget: float                   # target 1d 99% VaR ($)
    achieved_var99: float
    achieved_es975: float
    expected_annual_return: float       # mu*m ($)
    expected_return_pct: float          # / capital
    portfolio_vol_annual: float         # sqrt(252)*daily vol ($)
    return_on_var: float                # expected annual return / VaR budget
    gross_leverage: float
    net_notional: float
    binding_constraint: str             # "var_budget" | "leverage_cap" | ... | "stress_loss"
    sizes: list[TradeSize] = field(default_factory=list)
    # Stress-loss constraint (scenario-decision-impact); None when the solve had no stress input.
    stress_budget: float | None = None
    worst_stress_scenario: str | None = None
    worst_stress_loss: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "label": self.label,
            "capital_base": self.capital_base,
            "var_budget": self.var_budget,
            "achieved_var99": self.achieved_var99,
            "achieved_es975": self.achieved_es975,
            "expected_annual_return": self.expected_annual_return,
            "expected_return_pct": self.expected_return_pct,
            "portfolio_vol_annual": self.portfolio_vol_annual,
            "return_on_var": self.return_on_var,
            "gross_leverage": self.gross_leverage,
            "net_notional": self.net_notional,
            "binding_constraint": self.binding_constraint,
            "sizes": [s.as_dict() for s in self.sizes],
            **({"stress": {"budget": self.stress_budget, "worst_scenario": self.worst_stress_scenario,
                          "worst_loss": self.worst_stress_loss}} if self.stress_budget is not None else {}),
        }


def solve_profile(model: TradeRiskModel, mu: dict[str, ExpectedReturn],
                  profile: RiskProfile, capital_base: float,
                  pairs: list[dict[str, Any]] | None = None,
                  stress: StressInput | None = None) -> SizingResult:
    n = model.n_trades
    ids = model.trade_ids
    hedge_mask = np.array([t.is_hedge for t in model.trades])
    dir_idx = np.where(~hedge_mask)[0]

    # mu vector at current size, aligned to model order.
    mu_vec = np.array([mu[t].mu_annual if t in mu else 0.0 for t in ids])

    # 1. Directional risk budgets b.
    sub_cov = model.cov[np.ix_(dir_idx, dir_idx)]
    if profile.method == "erc" or profile.tilt_power <= 0.0:
        b = np.ones(len(dir_idx))
    else:
        # Sharpe per directional trade (annualised), floored & powered.
        sharpe = np.array([
            _sharpe(mu_vec[j], model.trades[j].standalone_vol) for j in dir_idx])
        pos = np.clip(sharpe, 0.0, None)
        b = np.power(pos, profile.tilt_power) + profile.sharpe_floor
    b = _apply_concentration_cap(b / b.sum(), profile.concentration_cap)

    x = _risk_budget(sub_cov, b)
    x = x / x.mean()                        # normalise directional multipliers to mean 1

    # 2. Fixed hedge block: hedges are held at hedge_scale x their current size (absolute),
    #    NOT scaled with the directional book — so scaling the book up to a higher VaR
    #    budget naturally thins the hedges (minimal in risky), while a hedge never inflates
    #    into a dominant directional short as the directional trades it offsets are resized.
    hedge_vec = np.zeros(n)
    hedge_vec[hedge_mask] = profile.hedge_scale
    dir_vec = np.zeros(n)
    dir_vec[dir_idx] = x                       # directional shape (mean-1 multipliers)

    # 3. Scale the directional block by scalar S so the whole book's 1-day 99% VaR hits
    #    the budget. VaR(S) = VaR(S*dir + hedge) is monotic in S; solve by bisection.
    var_budget = profile.var_budget_pct * capital_base
    s = _solve_var_scale(model, dir_vec, hedge_vec, var_budget)
    m = s * dir_vec + hedge_vec
    binding = "var_budget"

    # 4. Per-trade notional cap (position limit): no single trade's gross notional may
    #    exceed notional_cap_pct of capital. Prevents ultra-low-vol carry trades from
    #    being levered to absurd size to soak up risk budget.
    notional = np.array([t.notional for t in model.trades])
    cap_notional = profile.notional_cap_pct * capital_base
    with np.errstate(divide="ignore", invalid="ignore"):
        max_mult = np.where(np.abs(notional) > 0, cap_notional / np.abs(notional), np.inf)
    if (m > max_mult).any():
        m = np.minimum(m, max_mult)
        binding = "notional_cap"

    # 4b. Re-scale to the VaR budget. The notional cap can leave the book *above* budget when it
    #     trims a risk-*reducing* position (e.g. the SMH short that offsets the semis longs, or any
    #     satellite short), so if VaR still exceeds the budget, scale the whole book back down to
    #     it. VaR is positively homogeneous, so one multiplicative step lands it exactly; scaling
    #     down preserves the shape and keeps every notional under the cap. The book then respects
    #     its VaR mandate rather than overshooting whenever the cap binds on an offsetting leg.
    cur_var = model.var99(m)
    if cur_var > var_budget > 0:
        m = m * (var_budget / cur_var)
        binding = "var_budget"

    # 5. Gross-leverage guardrail: if the capped book still exceeds the leverage cap, scale
    #    the whole book down uniformly (VaR then lands below budget).
    lev_cap_gross = profile.gross_leverage_cap * capital_base
    m, lev_bound = _apply_leverage_cap(m, notional, lev_cap_gross)
    if lev_bound:
        binding = "leverage_cap"

    # 6. Market-neutral pair pins. A long/short cash pair (e.g. the N5 semis dispersion) can't be
    #    sized as a neutral unit by the return-tilt — it sizes the long up and floors the negative-
    #    carry short. So for each configured pair we pin the short leg's notional to `short_ratio` x
    #    the long leg's (beta-neutral when short_ratio = beta_long / beta_short), overriding that
    #    asymmetry so the pair expresses dispersion, not net beta. The short is a fraction (<1) of
    #    the long, which is already <= the notional cap, so this never breaches it; the book then
    #    runs at or just below its VaR budget (the pair carries little net risk).
    if pairs:
        idx = {t.trade_id: j for j, t in enumerate(model.trades)}
        for p in pairs:
            li, si = idx.get(str(p.get("long", ""))), idx.get(str(p.get("short", "")))
            if li is None or si is None or notional[si] == 0:
                continue
            m[si] = m[li] * (abs(notional[li]) / abs(notional[si])) * float(p.get("short_ratio", 1.0))
            binding = "market_neutral_pair"

    # 7. Realized single-name risk-share cap (final shaping step). `concentration_cap` (step 1) bounds
    #    each name's INTENDED share of *directional* risk (RC over the directional-only sub-covariance),
    #    but the REALIZED component-VaR share is measured against the hedged, lower *net-book* vol and
    #    runs materially higher — a 15% directional budget leaves the top single name (MU) at ~24% of
    #    net risk. Flatten any directional (non-hedge) name whose realized RC share exceeds
    #    `risk_share_cap`, then re-hit the VaR budget. Realized RC share is invariant to a uniform
    #    rescale, so the re-scale preserves both the cap AND any market-neutral pair's leg ratio (both
    #    legs scale together); this runs LAST so no later step re-disturbs the shares. Pair legs are
    #    excluded from the trim — they are sized as a net-neutral unit (step 6), so a leg's gross RC
    #    share is not the single-name concentration this bounds.
    if 0.0 < profile.risk_share_cap < 1.0:
        exclude: set[int] = set()
        if pairs:
            id2j = {t.trade_id: j for j, t in enumerate(model.trades)}
            for p in pairs:
                for leg in (str(p.get("long", "")), str(p.get("short", ""))):
                    if leg in id2j:
                        exclude.add(id2j[leg])
        m, capped = _apply_risk_share_cap(model, m, dir_idx, exclude, profile.risk_share_cap)
        if capped:
            m = model.scale_to_var_budget(m, var_budget)   # scale-invariant for RC shares + pair ratio
            m = np.minimum(m, max_mult)                    # keep the notional cap after the re-inflate
            binding = "risk_share_cap"

    # 8. Final leverage guardrail. Step 7's re-scale returns the book to its VaR budget, but when the
    #    leverage cap had bound at step 5 (VaR below budget) that re-scale inflates the book back past
    #    the cap. Re-apply the cap last so no step can leave the book above it. A uniform scale-down
    #    keeps realized RC shares, pair leg ratios and the notional cap intact; VaR then lands below
    #    budget and the leverage cap is the binding constraint.
    m, lev_bound = _apply_leverage_cap(m, notional, lev_cap_gross)
    if lev_bound:
        binding = "leverage_cap"

    # 9. Stress-loss constraint (scenario-decision-impact): the worst scenario loss of the sized book
    #    may not exceed the profile's stress budget. P&L is linear in size for a fixed scenario, so one
    #    homogeneous scale-down lands it on the budget and preserves every earlier shape (caps, pair
    #    ratios, risk shares); VaR then sits below its budget. Absent a stress input: unchanged.
    if stress is not None:
        s_stress = stress_scale(m, stress.pnl, stress.budget)
        if s_stress < 1.0:
            m = m * s_stress
            binding = "stress_loss"

    res = _assemble(model, mu_vec, m, profile, capital_base, var_budget, binding)
    if stress is not None:
        res.stress_budget = float(stress.budget)
        res.worst_stress_scenario, res.worst_stress_loss = worst_stress(m, stress.pnl, stress.names)
    return res


def _solve_var_scale(model: TradeRiskModel, dir_vec: np.ndarray, hedge_vec: np.ndarray,
                     target_var: float, iters: int = 60) -> float:
    """Smallest directional scale S with VaR(S*dir + hedge) = target (bisection)."""
    def var_at(s: float) -> float:
        return model.var99(s * dir_vec + hedge_vec)

    if var_at(0.0) >= target_var:            # hedges alone already exceed budget
        return 0.0
    lo, hi = 0.0, 1.0
    while var_at(hi) < target_var and hi < 1e6:
        hi *= 2.0
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if var_at(mid) < target_var:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# -- metrics assembly -------------------------------------------------------- #
def _assemble(model: TradeRiskModel, mu_vec: np.ndarray, m: np.ndarray,
              profile: RiskProfile, capital_base: float, var_budget: float,
              binding: str) -> SizingResult:
    ve = model.var_es(m)
    achieved_var = ve["var"]["0.99"]
    achieved_es = ve["es"]["0.975"]
    rc = model.risk_contributions(m)                       # daily-vol contributions (sum=vol)
    daily_vol = model.portfolio_vol(m)
    # Euler contributions sum to portfolio vol; a hedge's negative share means it reduces
    # risk. Share is signed and sums to 1 across the book.
    rc_share = rc / daily_vol if daily_vol > 0 else np.zeros_like(rc)

    notional = np.array([t.notional for t in model.trades])
    gross = float(np.abs(m * notional).sum())
    net = float((m * notional).sum())
    exp_ret = float(mu_vec @ m)

    sizes: list[TradeSize] = []
    for j, t in enumerate(model.trades):
        sizes.append(TradeSize(
            trade_id=t.trade_id,
            asset_class=t.asset_class,
            description=t.description,
            is_hedge=t.is_hedge,
            current_multiplier=1.0,
            multiplier=float(m[j]),
            current_notional=t.notional,
            target_notional=float(m[j] * t.notional),
            mu_annual=float(mu_vec[j] * m[j]),
            risk_contribution=float(rc[j]),
            risk_share=float(rc_share[j]),
            standalone_var99=float(m[j] * t.standalone_var99),
        ))
    sizes.sort(key=lambda s: -abs(s.risk_contribution))

    return SizingResult(
        profile=profile.key,
        label=profile.label,
        capital_base=capital_base,
        var_budget=var_budget,
        achieved_var99=achieved_var,
        achieved_es975=achieved_es,
        expected_annual_return=exp_ret,
        expected_return_pct=exp_ret / capital_base if capital_base else 0.0,
        portfolio_vol_annual=daily_vol * np.sqrt(_TRADING_DAYS),
        return_on_var=exp_ret / var_budget if var_budget else 0.0,
        gross_leverage=gross / capital_base if capital_base else 0.0,
        net_notional=net,
        binding_constraint=binding,
        sizes=sizes,
    )


# -- numerics ---------------------------------------------------------------- #
def _sharpe(mu_annual: float, daily_vol: float) -> float:
    ann_vol = daily_vol * np.sqrt(_TRADING_DAYS)
    return mu_annual / ann_vol if ann_vol > 0 else 0.0


def _apply_leverage_cap(m: np.ndarray, notional: np.ndarray,
                        lev_cap_gross: float) -> tuple[np.ndarray, bool]:
    """Scale ``m`` down uniformly so gross notional ``sum |m_j N_j|`` is at most ``lev_cap_gross``.

    Returns the (possibly) scaled multipliers and whether the cap bound. A non-positive cap is off.
    """
    gross = float(np.abs(m * notional).sum())
    if gross > lev_cap_gross > 0:
        return m * (lev_cap_gross / gross), True
    return m, False


def _apply_concentration_cap(b: np.ndarray, cap: float, iters: int = 100) -> np.ndarray:
    """Cap each budget share at ``cap`` and redistribute the excess to uncapped names."""
    if cap >= 1.0 or len(b) == 0:
        return b
    cap = max(cap, 1.0 / len(b))            # cap can't be below equal weight (infeasible)
    b = b.copy()
    for _ in range(iters):
        over = b > cap + 1e-12
        if not over.any():
            break
        excess = (b[over] - cap).sum()
        b[over] = cap
        under = ~over
        if not under.any():
            break
        b[under] += excess * b[under] / b[under].sum()
    return np.asarray(b / b.sum(), dtype=float)


def _apply_risk_share_cap(model: TradeRiskModel, m: np.ndarray, dir_idx: np.ndarray,
                          exclude: set[int], cap: float,
                          iters: int = 200, damp: float = 0.8,
                          tol: float = 1e-4) -> tuple[np.ndarray, bool]:
    """Trim any eligible directional trade whose *realized* component-VaR share exceeds ``cap``.

    Realized RC share is ``RC_j / portfolio_vol`` (the reported ``risk_share``), measured over the
    full hedged book. Because that ratio is invariant to a uniform rescale of ``m`` (RC_j and the vol
    both scale linearly), this only reshapes the *relative* sizes; the caller re-hits the VaR budget
    afterward without re-breaching the cap. Iterated with a damped multiplicative step because
    trimming the top name lifts every other name's share — the process is monotone on the over-cap set
    (sizes only ever shrink there) and settles with each eligible name at or below ``cap``. Hedges
    (outside ``dir_idx``) and ``exclude``d trades (market-neutral pair legs) are never trimmed.
    """
    m = m.copy()
    dir_set = {int(j) for j in dir_idx.tolist()}
    eligible = np.array([(j in dir_set) and (j not in exclude)
                         for j in range(model.n_trades)])
    if not eligible.any():
        return m, False
    capped = False
    for _ in range(iters):
        vol = model.portfolio_vol(m)
        if vol <= 0.0:
            break
        share = model.risk_contributions(m) / vol
        over = eligible & (share > cap + tol)
        if not over.any():
            break
        capped = True
        idx = np.where(over)[0]
        m[idx] = m[idx] * np.power(cap / share[idx], damp)
    return m, capped


def _risk_budget(cov: np.ndarray, budget: np.ndarray,
                 iters: int = 2000, tol: float = 1e-10) -> np.ndarray:
    """Solve min 1/2 xTSigmax - Sigma bi ln xi  (x>0) by cyclical coordinate descent (Spinu).

    At the optimum the component risk contributions satisfy RCi prop bi, i.e. a risk-budgeted
    (for equal b, risk-parity) long-only portfolio. Robust for the small dense Sigma here.
    """
    n = cov.shape[0]
    if n == 0:
        return np.zeros(0)
    b = budget / budget.sum()
    vol = np.sqrt(np.diag(cov))
    vol = np.where(vol > 0, vol, 1.0)
    x = b / vol                              # sensible warm start
    for _ in range(iters):
        x_prev = x.copy()
        for i in range(n):
            # Solve the scalar quadratic for x_i holding others fixed:
            #   Sigma_ii x_i² + (Sigma_{j≠i} Sigma_ij x_j) x_i - b_i = 0
            others = cov[i] @ x - cov[i, i] * x[i]
            a = cov[i, i]
            if a <= 0:
                continue
            x[i] = (-others + np.sqrt(others * others + 4 * a * b[i])) / (2 * a)
        if np.max(np.abs(x - x_prev)) < tol:
            break
    return np.asarray(x, dtype=float)
