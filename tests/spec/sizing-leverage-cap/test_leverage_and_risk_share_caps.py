"""sizing-leverage-cap C1-C2: when the gross-leverage cap and the risk-share cap both bind, both hold
and the leverage cap is reported as the binding constraint.

Every case is in the spec's "both caps bind" regime (docs/specs/sizing-leverage-cap.md, Notes): all
trades directional, `pairs=None`, the notional cap slack, one dominant-Sharpe name (`T0`) whose
intended risk budget share is far above `risk_share_cap`, and a leverage cap whose largest possible
VaR is below the VaR budget. Each test checks those preconditions by hand before it checks the result.
Gross leverage and risk shares are recomputed from `SizingResult.sizes` (see conftest.py).
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

LEV_REL = 1e-9   # spec C1: gross leverage <= cap within a relative tolerance of 1e-9


def _recipe(sz):
    r = sz.RECIPE
    book = sz.build_book(r.seed, [r.daily_vol] * r.n, [r.notional] * r.n)
    profile = sz.make_profile(r.var_budget_pct, r.gross_leverage_cap, r.risk_share_cap,
                              notional_cap_pct=r.notional_cap_pct)
    mu = sz.make_mu(book.ids, r.mu)
    return book, profile, mu, list(r.mu), r.capital_base


def _assert_both_caps_bind_regime(sz, book, profile, mu_annual, capital_base):
    """Preconditions (hand-computed, no optimizer): the leverage cap must limit the scale, and the
    dominant name's intended share is far above the risk-share cap, so the trim must act."""
    var_budget = profile.var_budget_pct * capital_base
    lev_gross = profile.gross_leverage_cap * capital_base
    assert sz.var_upper_bound(book, lev_gross) < var_budget, "precondition: leverage cap is not binding"
    assert sz.intended_top_share(book, mu_annual, profile) > profile.risk_share_cap + 0.2, \
        "precondition: risk-share cap is not binding"
    # Notional cap slack: no single trade of a book within the leverage cap can reach it.
    assert profile.notional_cap_pct > profile.gross_leverage_cap, "precondition: notional cap is not slack"


def _assert_c1(sz, book, profile, result, capital_base):
    lev = sz.gross_leverage(book, result, capital_base)
    assert lev <= profile.gross_leverage_cap * (1.0 + LEV_REL), \
        f"gross leverage {lev:.6f}x breaches the {profile.gross_leverage_cap}x cap"
    # Tolerance: the optimizer's documented trim tolerance (spec C1); 1e-12 absorbs float rounding in
    # the independent recomputation.
    limit = profile.risk_share_cap + sz.SHARE_TOL + 1e-12
    realized = sz.realized_shares(book, result)
    assert realized.max() <= limit, f"realized risk shares {np.round(realized, 6)} exceed {limit}"
    reported = sz.reported_shares(book, result)
    assert reported.max() <= limit, f"reported risk shares {np.round(reported, 6)} exceed {limit}"


def _assert_c2(sz, book, profile, result, capital_base):
    var_budget = profile.var_budget_pct * capital_base
    assert result.binding_constraint == "leverage_cap", \
        f"binding_constraint {result.binding_constraint!r}, expected 'leverage_cap'"
    # Requirement 2: the leverage cap limits the book exactly when gross leverage equals the cap.
    # Tolerance rel 1e-9 as in C1 (a uniform rescale lands on the cap up to float rounding).
    assert sz.gross_leverage(book, result, capital_base) == pytest.approx(profile.gross_leverage_cap, rel=LEV_REL)
    assert result.achieved_var99 < var_budget, f"achieved VaR {result.achieved_var99:,.0f} >= budget {var_budget:,.0f}"
    # Independent 1-day 99% VaR of the returned book from the test's own P&L matrix.
    m = sz.targets(book, result) / book.notionals
    own_var = float(np.quantile(-(book.pnl @ m), 0.99))
    assert own_var < var_budget, f"recomputed VaR {own_var:,.0f} >= budget {var_budget:,.0f}"


# ----------------------------------------------------------------------------------------- strategy


@st.composite
def _both_caps_bind(draw):
    """A random book and profile inside the spec's both-caps-bind regime.

    3-6 directional trades with zero sample correlation (orthogonalised columns, see conftest.py),
    daily vols $0.5-2mm and notionals $20-100mm; T0 has an annual Sharpe of 2-5 and every other name
    at most 0.013 (mu <= $0.1mm, vol >= $0.5mm), so T0's intended budget share is above 0.9 and the
    other names' budgets are within 2% of each other. The risk-share cap is between 1/n + 0.05 and
    0.5 (feasible: n * cap > 1), so once T0 is trimmed to the cap the other names, sharing the rest
    near-equally, stay below it. The VaR budget is 1.5-4x the largest VaR any book at the leverage cap can have, so the
    leverage cap (0.2-1.0x) always binds. The notional cap is slack and pairs=None.
    """
    n = draw(st.integers(min_value=3, max_value=6))
    seed = draw(st.integers(min_value=0, max_value=2**32 - 1))
    vols = draw(st.lists(st.floats(min_value=0.5e6, max_value=2e6), min_size=n, max_size=n))
    notionals = draw(st.lists(st.floats(min_value=20e6, max_value=100e6), min_size=n, max_size=n))
    sharpe0 = draw(st.floats(min_value=2.0, max_value=5.0))
    others = draw(st.lists(st.floats(min_value=0.0, max_value=0.1e6), min_size=n - 1, max_size=n - 1))
    mu_annual = [sharpe0 * vols[0] * np.sqrt(252), *others]
    lev_cap = draw(st.floats(min_value=0.2, max_value=1.0))
    budget_factor = draw(st.floats(min_value=1.5, max_value=4.0))
    share_cap = draw(st.floats(min_value=1.0 / n + 0.05, max_value=0.5))
    return {"seed": seed, "vols": vols, "notionals": notionals, "mu_annual": mu_annual, "lev_cap": lev_cap,
            "budget_factor": budget_factor, "share_cap": share_cap}


def _build(sz, case):
    capital_base = 1e9
    book = sz.build_book(case["seed"], case["vols"], case["notionals"], orthogonal=True)
    bound = sz.var_upper_bound(book, case["lev_cap"] * capital_base)
    var_budget_pct = case["budget_factor"] * bound / capital_base
    profile = sz.make_profile(var_budget_pct, case["lev_cap"], case["share_cap"])
    mu = sz.make_mu(book.ids, case["mu_annual"])
    return book, profile, mu, case["mu_annual"], capital_base


# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_spec_recipe_holds_leverage_cap_and_risk_share_cap(sz):
    # oracle: mandate definitions in profiles.py (gross_leverage_cap = max gross notional / capital;
    # risk_share_cap = max directional name's realized net-book risk share) applied to the spec's
    # hand-built recipe: gross <= 0.5x ($500mm on $1bn) and every share <= 0.30 + 1e-4.
    book, profile, mu, mu_annual, capital_base = _recipe(sz)
    _assert_both_caps_bind_regime(sz, book, profile, mu_annual, capital_base)
    result = sz.solve(book, mu, profile, capital_base)
    _assert_c1(sz, book, profile, result, capital_base)


@pytest.mark.criterion("C1")
@given(case=_both_caps_bind())
def test_c1_property_both_caps_hold_whenever_both_bind(sz, case):
    # oracle: invariant (spec C1): Σ|target_notional|/capital <= gross_leverage_cap (rel 1e-9) and every
    # directional trade's Euler risk share <= risk_share_cap + 1e-4, for any book in the both-bind regime.
    book, profile, mu, mu_annual, capital_base = _build(sz, case)
    _assert_both_caps_bind_regime(sz, book, profile, mu_annual, capital_base)
    result = sz.solve(book, mu, profile, capital_base)
    _assert_c1(sz, book, profile, result, capital_base)


# ----------------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
def test_c2_spec_recipe_reports_leverage_cap_and_var_below_budget(sz):
    # oracle: meaning of a binding constraint (spec C2 / Requirement 2): the recipe's 0.5x cap allows at
    # most $500mm gross, whose VaR is provably below the $50mm budget (var_upper_bound), so the leverage
    # cap sets the scale: binding_constraint == "leverage_cap", gross == 0.5x and VaR99 < $50mm.
    book, profile, mu, mu_annual, capital_base = _recipe(sz)
    _assert_both_caps_bind_regime(sz, book, profile, mu_annual, capital_base)
    result = sz.solve(book, mu, profile, capital_base)
    # var_budget = 0.05 x $1bn = $50mm (hand calculation); tolerance rel 1e-12 is float rounding only.
    assert result.var_budget == pytest.approx(50e6, rel=1e-12)
    _assert_c2(sz, book, profile, result, capital_base)


@pytest.mark.criterion("C2")
@given(case=_both_caps_bind())
def test_c2_property_leverage_cap_reported_whenever_both_bind(sz, case):
    # oracle: invariant (spec C2): in the both-bind regime the leverage cap sets the scale, so the result
    # reports "leverage_cap", sits on the cap, and its 1-day 99% VaR is strictly below the budget.
    book, profile, mu, mu_annual, capital_base = _build(sz, case)
    _assert_both_caps_bind_regime(sz, book, profile, mu_annual, capital_base)
    result = sz.solve(book, mu, profile, capital_base)
    _assert_c2(sz, book, profile, result, capital_base)
