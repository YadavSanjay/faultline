"""Hand-built inputs and hand oracles for the scenario-stability spec tests.

Everything is synthetic (docs/specs/scenario-stability.md, Notes). ``leg_records`` is the C2 input
(scenario A resolved in repetitions 0 and 1 and stopped in 2, scenario B resolved in repetition 1 only
with no legs); ``leg_expected`` is Requirement 2 applied to it by hand. ``pnl_records`` is the C3 input
(A at mild priced three times, A at adverse with repetition 1 missing, B at mild priced once, records
interleaved) and ``pnl_expected`` is Requirement 3 applied by hand.

``entry`` builds one ``spread`` result from hand-computed statistics. ``assert_close`` compares
structures with float leaves through ``pytest.approx``. Helpers are exposed through the
session-scoped ``h`` fixture (session scope so hypothesis tests can use it).
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

# Notes of the spec: expected floats compare with relative 1e-12, absolute 1e-15 (only float rounding
# in a sum, a difference or a square root separates the implementation from the hand value).
REL = 1e-12
ABS = 1e-15


def entry(values: list[float | None], *, mn: float | None, mx: float | None, rng: float | None,
          mean: float | None, std: float | None, rel_range: float | None, sign_flip: bool,
          **extra: Any) -> dict[str, Any]:
    """One ``spread`` result built from hand-computed statistics (``extra`` adds e.g. ``n_missing``)."""
    out: dict[str, Any] = {
        "n": len(values), "n_present": sum(v is not None for v in values), "values": list(values),
        "min": mn, "max": mx, "range": rng, "mean": mean, "std": std, "rel_range": rel_range,
        "sign_flip": sign_flip,
    }
    out.update(extra)
    return out


def leg_records() -> list[dict[str, Any]]:
    """C2 input: two scenarios over three repetitions; B's first record sits between A's."""
    return [
        {"repetition": 0, "scenario": "A",
         "legs": {"macrorag": {"ir:USD10Y": 0.0040, "eq:XLK": -0.08}, "xbrlrag": {"eq:NVDA": -0.20}}},
        {"repetition": 1, "scenario": "B", "legs": {}},
        {"repetition": 1, "scenario": "A",
         "legs": {"macrorag": {"ir:USD10Y": 0.0050}, "xbrlrag": {"eq:NVDA": -0.26, "eq:AMD": -0.15}}},
        {"repetition": 2, "scenario": "A", "stopped": True},
    ]


def leg_expected() -> list[dict[str, Any]]:
    """Requirement 2 by hand on ``leg_records`` with ``n_repetitions = 3``, sources macrorag / xbrlrag.

    A: resolved in 0 and 1 (n_resolved 2), stopped in 2 (n_stopped 1).
      macrorag legs = {ir:USD10Y, eq:XLK}, sorted -> eq:XLK, ir:USD10Y.
        eq:XLK   [-0.08, None, None]: one present value -> min = max = mean = -0.08, range 0.0, std None,
                 rel_range 0.0 / 0.08 = 0.0; n_missing = 2 - 1 = 1.
        ir:USD10Y [0.0040, 0.0050, None]: range 0.0010, mean 0.0045, deviations +-0.0005 ->
                 std = sqrt(2 * 0.0005**2 / 1) = 0.0005 * sqrt(2); rel_range 0.0010 / 0.0050 = 0.2;
                 n_missing 0.
      xbrlrag legs = {eq:NVDA, eq:AMD}, sorted -> eq:AMD, eq:NVDA.
        eq:AMD   [None, -0.15, None]: one value; n_missing 1.
        eq:NVDA  [-0.20, -0.26, None]: range 0.06, mean -0.23, std 0.03 * sqrt(2), rel_range 0.06 / 0.26;
                 n_missing 0.
    B: resolved in 1 only (n_resolved 1, n_stopped 2: repetitions 0 and 2 have no record); no legs ->
      {} for both sources.
    """
    a_macro = {
        "eq:XLK": entry([-0.08, None, None], mn=-0.08, mx=-0.08, rng=0.0, mean=-0.08, std=None,
                        rel_range=0.0, sign_flip=False, n_missing=1),
        "ir:USD10Y": entry([0.0040, 0.0050, None], mn=0.0040, mx=0.0050, rng=0.0010, mean=0.0045,
                           std=0.0005 * math.sqrt(2.0), rel_range=0.0010 / 0.0050, sign_flip=False,
                           n_missing=0),
    }
    a_xbrl = {
        "eq:AMD": entry([None, -0.15, None], mn=-0.15, mx=-0.15, rng=0.0, mean=-0.15, std=None,
                        rel_range=0.0, sign_flip=False, n_missing=1),
        "eq:NVDA": entry([-0.20, -0.26, None], mn=-0.26, mx=-0.20, rng=0.06, mean=-0.23,
                         std=0.03 * math.sqrt(2.0), rel_range=0.06 / 0.26, sign_flip=False, n_missing=0),
    }
    return [
        {"scenario": "A", "n_repetitions": 3, "n_resolved": 2, "n_stopped": 1,
         "macrorag": a_macro, "xbrlrag": a_xbrl},
        {"scenario": "B", "n_repetitions": 3, "n_resolved": 1, "n_stopped": 2, "macrorag": {}, "xbrlrag": {}},
    ]


def pnl_records() -> list[dict[str, Any]]:
    """C3 input, interleaved so first appearance is (A, mild), (A, adverse), (B, mild)."""
    return [
        {"repetition": 0, "scenario": "A", "severity": "mild", "total_pnl": -1.0e6},
        {"repetition": 0, "scenario": "A", "severity": "adverse", "total_pnl": -2.0e6},
        {"repetition": 1, "scenario": "B", "severity": "mild", "total_pnl": -4.0e5},
        {"repetition": 1, "scenario": "A", "severity": "mild", "total_pnl": -1.2e6},
        {"repetition": 2, "scenario": "A", "severity": "adverse", "total_pnl": -2.5e6},
        {"repetition": 2, "scenario": "A", "severity": "mild", "total_pnl": -0.9e6},
    ]


def pnl_row(scenario: str, severity: str, values: list[float | None], **stats: Any) -> dict[str, Any]:
    e = entry(values, **stats)
    row = {"scenario": scenario, "severity": severity, "n_repetitions": len(values),
           "n_priced": e["n_present"], "n_missing": len(values) - e["n_present"]}
    row.update(e)
    return row


def pnl_expected() -> list[dict[str, Any]]:
    """Requirement 3 by hand on ``pnl_records`` with ``n_repetitions = 3``.

    (A, mild) [-1.0e6, -1.2e6, -0.9e6]: min -1.2e6, max -0.9e6, range 3.0e5, mean -3.1e6 / 3;
      deviations in units of 1e5: +1/3, -5/3, +4/3 -> squares 1/9 + 25/9 + 16/9 = 42/9, / (3 - 1) = 7/3
      -> std = 1e5 * sqrt(7/3) ~ 152752.5; rel_range 3.0e5 / 1.2e6 = 0.25; n_priced 3, n_missing 0.
    (A, adverse) [-2.0e6, None, -2.5e6]: range 5.0e5, mean -2.25e6, deviations +-2.5e5 ->
      std = 2.5e5 * sqrt(2); rel_range 5.0e5 / 2.5e6 = 0.2; n_priced 2, n_missing 1.
    (B, mild) [None, -4.0e5, None]: priced once -> range 0.0, std None, rel_range 0.0; n_priced 1,
      n_missing 2.
    """
    return [
        pnl_row("A", "mild", [-1.0e6, -1.2e6, -0.9e6], mn=-1.2e6, mx=-0.9e6, rng=3.0e5, mean=-3.1e6 / 3,
                std=1.0e5 * math.sqrt(7.0 / 3.0), rel_range=0.25, sign_flip=False),
        pnl_row("A", "adverse", [-2.0e6, None, -2.5e6], mn=-2.5e6, mx=-2.0e6, rng=5.0e5, mean=-2.25e6,
                std=2.5e5 * math.sqrt(2.0), rel_range=0.2, sign_flip=False),
        pnl_row("B", "mild", [None, -4.0e5, None], mn=-4.0e5, mx=-4.0e5, rng=0.0, mean=-4.0e5, std=None,
                rel_range=0.0, sign_flip=False),
    ]


def assert_close(actual: Any, expected: Any, path: str = "$", *, rel: float = REL, abs_: float = ABS) -> None:
    """Structural equality with float leaves compared by ``pytest.approx(rel=rel, abs=abs_)``.

    Bools, ints and strings must match exactly (an int is not a bool); ``None`` must stay ``None``; a
    float leaf must be a ``float`` instance (Requirement 1 converts numbers to ``float``).
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected dict, got {actual!r}"
        assert set(actual) == set(expected), f"{path}: keys {sorted(actual)} != {sorted(expected)}"
        for k in expected:
            assert_close(actual[k], expected[k], f"{path}.{k}", rel=rel, abs_=abs_)
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected list, got {actual!r}"
        assert len(actual) == len(expected), f"{path}: length {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert_close(a, e, f"{path}[{i}]", rel=rel, abs_=abs_)
    elif expected is None or isinstance(expected, (bool, str)):
        assert actual == expected and type(actual) is type(expected), f"{path}: {actual!r} != {expected!r}"
    elif isinstance(expected, int):
        assert actual == expected and not isinstance(actual, bool), f"{path}: {actual!r} != {expected!r}"
    else:
        assert isinstance(actual, float), f"{path}: expected a float, got {actual!r}"
        assert actual == pytest.approx(expected, rel=rel, abs=abs_), f"{path}: {actual!r} != {expected!r}"


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        rel=REL,
        abs=ABS,
        entry=entry,
        leg_records=leg_records,
        leg_expected=leg_expected,
        pnl_records=pnl_records,
        pnl_row=pnl_row,
        pnl_expected=pnl_expected,
        assert_close=assert_close,
    )
