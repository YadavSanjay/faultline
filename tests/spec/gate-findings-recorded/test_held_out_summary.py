"""gate-findings-recorded C2: ``report.held_out_summary`` renders a held-out entry as one log line.

Oracle: Requirement 2 by hand: ``"<n_fail> fail / <n> finding(s): "`` then up to ``limit`` findings in
entry order as ``"<factor> <check> <observed> vs <limit>"`` (``:.4g``) joined by ``"; "``, then
``" (+<k> more)"`` when ``k > 0`` are left out; ``limit < 1`` raises ``ValueError``.

The ``:.4g`` renderings of the conftest numbers, worked by hand (4 significant digits, trailing zeros
dropped, exponent form once the decimal exponent reaches 4):
  0.0123456 -> 0.01235    0.01 -> 0.01    250.0 -> 250          200.0 -> 200
  -0.2345678 -> -0.2346   0.15 -> 0.15    0.0812345 -> 0.08123  0.06 -> 0.06
  -0.45 -> -0.45          12345.6 -> 1.235e+04                  10000.0 -> 1e+04
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.report import held_out_summary

HEAD = "2 fail / 6 finding(s): "
P1 = "IR_USD_10Y bound 0.01235 vs 0.01"
P2 = "CR_IG bound 250 vs 200"
P3 = "EQ_SPX envelope -0.2346 vs 0.15"
P4 = "FX_EURUSD envelope 0.08123 vs 0.06"
P5 = "EQ_NVDA market_implied -0.45 vs 0.01"
P6 = "VOL_VIX bound 1.235e+04 vs 1e+04"

FINITE = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e9, max_value=1e9)
FINDING = st.fixed_dictionaries({
    "factor": st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789", min_size=1, max_size=12),
    "check": st.sampled_from(["bound", "envelope"]),
    "observed": FINITE,
    "limit": FINITE,
    "severity": st.sampled_from(["fail", "flag", "info"]),
})


@pytest.mark.criterion("C2")
def test_c2_point_more_than_limit_has_more_suffix(h):
    # oracle: Requirement 2 by hand: 6 findings (2 fail), default limit 5 -> the first five in entry
    #   order, then " (+1 more)".
    line = held_out_summary(h.mixed_entry())
    assert line == HEAD + "; ".join([P1, P2, P3, P4, P5]) + " (+1 more)"


@pytest.mark.criterion("C2")
def test_c2_point_limit_one_keeps_only_first(h):
    # oracle: Requirement 2 by hand: limit=1 -> the first finding only, 5 left out -> " (+5 more)".
    assert held_out_summary(h.mixed_entry(), limit=1) == HEAD + P1 + " (+5 more)"


@pytest.mark.criterion("C2")
def test_c2_point_exactly_limit_has_no_suffix(h):
    # oracle: Requirement 2 by hand: 6 findings, limit=6 -> all six, nothing left out, no suffix.
    assert held_out_summary(h.mixed_entry(), limit=6) == HEAD + "; ".join([P1, P2, P3, P4, P5, P6])


@pytest.mark.criterion("C2")
def test_c2_point_fewer_than_limit_has_no_suffix(h):
    # oracle: Requirement 2 by hand: 6 findings, limit=10 -> all six, no suffix.
    assert held_out_summary(h.mixed_entry(), limit=10) == HEAD + "; ".join([P1, P2, P3, P4, P5, P6])


@pytest.mark.criterion("C2")
def test_c2_point_single_flag_finding_counts_zero_fails():
    # oracle: Requirement 2 by hand: one flag finding, default limit 5 -> "0 fail / 1 finding(s): " and
    #   its part; -1.5 -> "-1.5", 1.0 -> "1" under :.4g; no suffix.
    entry = {"scenario": "taiwan", "severity": "adverse", "scenario_version": "v2", "status": "flag",
             "findings": [{"factor": "EQ_TSM", "check": "envelope", "observed": -1.5, "limit": 1.0,
                           "severity": "flag"}]}
    assert held_out_summary(entry) == "0 fail / 1 finding(s): EQ_TSM envelope -1.5 vs 1"


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("bad", [0, -1, -5])
def test_c2_point_limit_below_one_raises(h, bad):
    # oracle: Requirement 2: "limit < 1 raises ValueError".
    with pytest.raises(ValueError, match=r"\S"):
        held_out_summary(h.mixed_entry(), limit=bad)


@pytest.mark.criterion("C2")
def test_c2_point_entry_not_mutated(h):
    # oracle: Requirement 2 renders the entry and never asks to change it: the entry equals a deepcopy
    #   taken before the call, and the line is the hand-written limit=2 line.
    entry = h.mixed_entry()
    before = copy.deepcopy(entry)
    line = held_out_summary(entry, limit=2)
    assert entry == before
    assert line == HEAD + P1 + "; " + P2 + " (+4 more)"


@pytest.mark.criterion("C2")
@given(findings=st.lists(FINDING, min_size=1, max_size=12), limit=st.integers(min_value=1, max_value=8))
def test_c2_property_prefix_and_part_count(findings, limit):
    # oracle: Requirement 2: the line starts with "<n_fail> fail / <n> finding(s): " for the entry's
    #   counts (counted here from the generated severities), carries min(n, limit) <= limit "; "-separated
    #   parts in entry order, and ends with " (+<n-limit> more)" exactly when n > limit. Factor names and
    #   checks contain no "; " and :.4g output contains no "; ", so the split is unambiguous.
    entry = {"scenario": "s", "severity": "severe", "scenario_version": "v1", "status": "fail",
             "findings": findings}
    n = len(findings)
    n_fail = sum(1 for f in findings if f["severity"] == "fail")
    head = f"{n_fail} fail / {n} finding(s): "
    line = held_out_summary(entry, limit=limit)
    assert line.startswith(head)
    rest = line[len(head):]
    if n > limit:
        suffix = f" (+{n - limit} more)"
        assert rest.endswith(suffix)
        rest = rest[: -len(suffix)]
    else:
        assert " more)" not in rest
    parts = rest.split("; ")
    assert len(parts) == min(n, limit)
    assert len(parts) <= limit
    for part, f in zip(parts, findings, strict=False):
        assert part.startswith(f"{f['factor']} {f['check']} ")
        assert " vs " in part
