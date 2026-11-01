"""Hand-built inputs and hand oracles for the measurement-views spec tests.

Everything is synthetic (docs/specs/measurement-views.md, Notes). Shares, losses, cosines and size
ratios are small dyadic fractions, so sums, differences and the means used here are exact in binary;
``assert_close`` still compares float leaves with ``pytest.approx`` (summation order may differ).

Report shapes follow the Requirement's field lists (coverage / decision impact / event replay reports);
extra keys the real runners write (``variance``, ``book_exposure``, ``constrained`` ...) are included so
the views are seen to pick only the keys the Requirement names.

The standard book order (Requirement 2) is written out literally here (``STANDARD_BOOKS``).

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

STANDARD_BOOKS = ("balanced", "core", "aggressive", "risky")


# ------------------------------------------------------------------------------------------- coverage
def direction(index: int, variance_share: float, spanned_share: float, uncovered: bool,
              top_loadings: list[dict[str, Any]]) -> dict[str, Any]:
    """One report direction, with the extra keys ``coverage_runner`` writes (``variance``, ``book_exposure``)."""
    return {"index": index, "variance": 4.0 * variance_share, "variance_share": variance_share,
            "spanned_share": spanned_share, "uncovered": uncovered, "book_exposure": -0.5,
            "top_loadings": top_loadings}


def coverage_report() -> dict[str, Any]:
    """3 directions given in index order 2, 3, 1 (index 2 uncovered), 3 losses with a tie, one merge pair."""
    return {
        "run_id": "cov-1", "stress_run_id": "stress-1", "valuation_date": "2026-10-06",
        "generated_at": "2026-10-19T09:00:00", "tier": "adverse",
        "coverage": 0.75, "n_directions": 3,
        "directions": [
            direction(2, 0.125, 0.25, True, [{"trade_id": "T3", "loading": -0.5}]),
            direction(3, 0.0625, 0.875, False, [{"trade_id": "T2", "loading": 0.25}]),
            direction(1, 0.5, 0.9375, False, [{"trade_id": "T1", "loading": 0.75},
                                              {"trade_id": "T2", "loading": -0.25}]),
        ],
        "diversity": {"participation_ratio": 2.5, "target": [2.0, 4.0], "in_target": True,
                      "losses": {"taiwan": 8.0, "ai_bust": 12.5, "rates_up": 8.0}},
        "cosine": {"ai_bust": {"taiwan": 0.875}},
        "merge_pairs": [{"a": "ai_bust", "b": "taiwan", "cosine": 0.875}],
        "missing": ["credit_crunch"],
    }


def coverage_view() -> dict[str, Any]:
    """Requirement 1 by hand for ``coverage_report``.

    directions sorted by index 1, 2, 3 with the five named keys; uncovered_share = 0.125 (index 2 only);
    losses by loss descending then name: ai_bust 12.5, then the 8.0 tie rates_up < taiwan.
    """
    return {
        "coverage": 0.75, "n_directions": 3, "uncovered_share": 0.125,
        "directions": [
            {"index": 1, "variance_share": 0.5, "spanned_share": 0.9375, "uncovered": False,
             "top_loadings": [{"trade_id": "T1", "loading": 0.75}, {"trade_id": "T2", "loading": -0.25}]},
            {"index": 2, "variance_share": 0.125, "spanned_share": 0.25, "uncovered": True,
             "top_loadings": [{"trade_id": "T3", "loading": -0.5}]},
            {"index": 3, "variance_share": 0.0625, "spanned_share": 0.875, "uncovered": False,
             "top_loadings": [{"trade_id": "T2", "loading": 0.25}]},
        ],
        "diversity": {"participation_ratio": 2.5, "target": [2.0, 4.0], "in_target": True},
        "losses": [{"scenario": "ai_bust", "loss": 12.5}, {"scenario": "rates_up", "loss": 8.0},
                   {"scenario": "taiwan", "loss": 8.0}],
        "merge_pairs": [{"a": "ai_bust", "b": "taiwan", "cosine": 0.875}],
        "missing": ["credit_crunch"],
    }


# ------------------------------------------------------------------------------------------- decision
def profile(budget: float, worst: str, worst_loss: float, over: list[str], b_from: str, b_to: str,
            n_changed: int) -> dict[str, Any]:
    """One ``sets[].profiles[book]`` entry; ``constrained`` holds decoy values the view must not use."""
    return {"stress_budget": budget,
            "unconstrained": {"binding": b_from, "worst_scenario": worst, "worst_loss": worst_loss,
                              "over_budget": list(over)},
            "constrained": {"binding": b_to, "worst_scenario": "decoy", "worst_loss": -1.0,
                            "over_budget": ["decoy"] * 7},
            "change": {"binding": {"from": b_from, "to": b_to}, "n_trades_changed": n_changed,
                       "multipliers": {}}}


def compare(b_from: str, b_to: str, ratios: dict[str, float]) -> dict[str, Any]:
    """One ``without_each_source[source][book]`` entry; ``from``/``to`` multipliers are arbitrary."""
    return {"binding": {"from": b_from, "to": b_to}, "n_trades_changed": len(ratios),
            "multipliers": {t: {"from": 2.0, "to": 2.0 * r, "ratio": r} for t, r in ratios.items()},
            "var99": {"from": 1.0, "to": 1.0}}


def decision_report() -> dict[str, Any]:
    """Ablation set first (ignored), then the stress set over the four standard books + ``income``."""
    ablation_profiles = {b: profile(1.0, "ignored", 1.0, ["ignored"], "x", "y", 99)
                         for b in ("balanced", "core", "zeta")}
    stress_profiles = {
        "risky": profile(64.0, "taiwan", 96.0, ["taiwan", "ai_bust"], "var_budget", "stress", 5),
        "income": profile(8.0, "rates_up", 6.0, [], "gross_leverage", "gross_leverage", 0),
        "aggressive": profile(32.0, "ai_bust", 40.0, ["ai_bust"], "var_budget", "stress", 3),
        "balanced": profile(16.0, "taiwan", 12.0, [], "var_budget", "var_budget", 0),
        "core": profile(24.0, "ai_bust", 30.5, ["ai_bust", "taiwan", "rates_up"], "es_budget", "stress", 2),
    }
    return {
        "run_id": "dec-1", "valuation_date": "2026-10-06", "generated_at": "2026-10-19T09:30:00",
        "tier": "adverse", "var_multiple": 3.0, "capital_base": 1000.0,
        "sets": [
            {"kind": "ablation", "label": "without:macrorag", "layers": ["xbrlrag"], "profiles": ablation_profiles},
            {"kind": "stress", "label": "authored", "layers": None, "profiles": stress_profiles},
        ],
        "without_each_source": {
            "xbrlrag": {
                "risky": compare("stress", "var_budget", {"T1": 0.5, "T2": 1.5, "T3": 0.25}),
                "core": compare("stress", "stress", {"T4": 2.0}),
                "balanced": compare("var_budget", "var_budget", {"T1": 0.75, "T2": 1.25}),
            },
            "macrorag": {
                "income": compare("gross_leverage", "stress", {"T5": 0.5}),
                "aggressive": compare("stress", "stress", {}),
                "balanced": compare("var_budget", "es_budget", {"T1": 1.0, "T2": 0.5, "T3": 0.75, "T4": 0.25}),
            },
        },
    }


def decision_view() -> dict[str, Any]:
    """Requirement 2 by hand for ``decision_report``.

    books: balanced, core, aggressive, risky, then income; n_over_budget = len(unconstrained.over_budget).
    without, sources in report order (xbrlrag, macrorag):
      xbrlrag: balanced (0.75 + 1.25) / 2 = 1.0; core 2.0 / 1 = 2.0; risky (0.5 + 1.5 + 0.25) / 3 = 0.75;
      macrorag: balanced (1.0 + 0.5 + 0.75 + 0.25) / 4 = 0.625; aggressive no multipliers -> 1.0;
        income 0.5 / 1 = 0.5.
    """
    def book(name: str, budget: float, worst: str, loss: float, b_from: str, b_to: str, n_over: int,
             n_changed: int) -> dict[str, Any]:
        return {"book": name, "stress_budget": budget, "worst_scenario": worst, "worst_loss": loss,
                "binding_from": b_from, "binding_to": b_to, "n_over_budget": n_over, "n_trades_changed": n_changed}

    def wb(name: str, b_from: str, b_to: str, ratio: float) -> dict[str, Any]:
        return {"book": name, "binding_from": b_from, "binding_to": b_to, "binding_changed": b_from != b_to,
                "size_ratio": ratio}

    return {
        "tier": "adverse",
        "books": [
            book("balanced", 16.0, "taiwan", 12.0, "var_budget", "var_budget", 0, 0),
            book("core", 24.0, "ai_bust", 30.5, "es_budget", "stress", 3, 2),
            book("aggressive", 32.0, "ai_bust", 40.0, "var_budget", "stress", 1, 3),
            book("risky", 64.0, "taiwan", 96.0, "var_budget", "stress", 2, 5),
            book("income", 8.0, "rates_up", 6.0, "gross_leverage", "gross_leverage", 0, 0),
        ],
        "without": [
            {"source": "xbrlrag", "books": [wb("balanced", "var_budget", "var_budget", 1.0),
                                            wb("core", "stress", "stress", 2.0),
                                            wb("risky", "stress", "var_budget", 0.75)]},
            {"source": "macrorag", "books": [wb("balanced", "var_budget", "es_budget", 0.625),
                                             wb("aggressive", "stress", "stress", 1.0),
                                             wb("income", "gross_leverage", "stress", 0.5)]},
        ],
    }


# --------------------------------------------------------------------------------------------- replay
def version(nearest: str | None, cosines: dict[str, float | None]) -> dict[str, Any]:
    """One ``versions.authored|enriched`` block (scores carry an extra ``rank`` key as decoration)."""
    return {"nearest": nearest,
            "scores": {n: {"cosine": c, "rank": i + 1, "sleeves": {}} for i, (n, c) in enumerate(cosines.items())}}


def replay_report() -> dict[str, Any]:
    """Two episodes: covid (both scored, gain +0.25, with pit) and gfc (enriched nearest None, no pit).

    No ``limitations`` key.
    """
    return {
        "run_id": "rep-1", "generated_at": "2026-10-19T10:00:00",
        "episodes": [
            {"name": "covid_2020", "as_of": "2020-02-21", "horizon_days": 20, "tier": "adverse",
             "available": ["equity_crash", "rates_up"],
             "dropped": [{"scenario": "taiwan", "version": "authored", "reason": "replay_overlap", "detail": ""},
                         {"scenario": "ai_bust", "version": "enriched", "reason": "gate", "detail": "x"}],
             "versions": {"authored": version("equity_crash", {"rates_up": 0.25, "equity_crash": 0.5}),
                          "enriched": version("equity_crash", {"equity_crash": 0.75, "rates_up": 0.125})},
             "pit": {"regime_asof": "2020-02-14", "dropped_issuer_factors": ["CR_ORCL", "CR_META"]}},
            {"name": "gfc_2008", "as_of": "2008-09-12", "horizon_days": 10, "tier": "adverse",
             "available": ["credit_crunch"], "dropped": [],
             "versions": {"authored": version("credit_crunch", {"credit_crunch": 0.625}),
                          "enriched": version(None, {"credit_crunch": None})}},
        ],
    }


def replay_view() -> dict[str, Any]:
    """Requirement 3 by hand for ``replay_report``.

    covid: authored cosine scores[equity_crash] = 0.5, enriched 0.75 -> gain 0.75 - 0.5 = 0.25;
    gfc: enriched nearest None -> cosine None -> gain None; pit absent -> None / []; limitations [].
    """
    return {
        "limitations": [],
        "episodes": [
            {"name": "covid_2020", "as_of": "2020-02-21", "horizon_days": 20,
             "authored": {"nearest": "equity_crash", "cosine": 0.5},
             "enriched": {"nearest": "equity_crash", "cosine": 0.75}, "gain": 0.25,
             "available": ["equity_crash", "rates_up"], "dropped": ["taiwan", "ai_bust"],
             "regime_asof": "2020-02-14", "dropped_issuer_factors": ["CR_ORCL", "CR_META"]},
            {"name": "gfc_2008", "as_of": "2008-09-12", "horizon_days": 10,
             "authored": {"nearest": "credit_crunch", "cosine": 0.625},
             "enriched": {"nearest": None, "cosine": None}, "gain": None,
             "available": ["credit_crunch"], "dropped": [],
             "regime_asof": None, "dropped_issuer_factors": []},
        ],
    }


# ------------------------------------------------------------------------------------------- helpers
def assert_close(actual: Any, expected: Any, path: str = "$") -> None:
    """Structural equality with float leaves compared by ``pytest.approx``.

    Tolerance rel=1e-12, abs=1e-12: the hand values are dyadic, so only summation order may differ.
    Bools, ints, strings and ``None`` must match exactly.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected dict, got {actual!r}"
        assert set(actual) == set(expected), f"{path}: keys {sorted(actual)} != {sorted(expected)}"
        for k in expected:
            assert_close(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected list, got {actual!r}"
        assert len(actual) == len(expected), f"{path}: length {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert_close(a, e, f"{path}[{i}]")
    elif expected is None or isinstance(expected, (bool, str)):
        assert actual == expected and type(actual) is type(expected), f"{path}: {actual!r} != {expected!r}"
    elif isinstance(expected, int):
        assert actual == expected and not isinstance(actual, bool), f"{path}: {actual!r} != {expected!r}"
    else:
        assert actual is not None and not isinstance(actual, bool), f"{path}: {actual!r} != {expected!r}"
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12), f"{path}: {actual!r} != {expected!r}"


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        standard_books=STANDARD_BOOKS,
        direction=direction,
        coverage_report=coverage_report,
        coverage_view=coverage_view,
        profile=profile,
        compare=compare,
        decision_report=decision_report,
        decision_view=decision_view,
        version=version,
        replay_report=replay_report,
        replay_view=replay_view,
        assert_close=assert_close,
    )
