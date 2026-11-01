"""mcp-call-timeouts C1-C3: ``CallTimeoutPolicy`` lookup, validation and per-tool degradation.

Every expected value is the spec's own rule (docs/specs/mcp-call-timeouts.md, Requirements 1-3) applied by
hand to settings dicts built in the test. Tool names are namespaced (``<prefix>__<tool>``) as in
``phase_allowlists``. Defaults (Requirement 3): ``timeout_s`` 600, ``call_timeout_s`` 90,
``tool_timeouts_s`` {}, ``degrade_after_timeouts`` 1.
"""

from __future__ import annotations

import math
import re
from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.scenariogen.llm.mcp.timeouts import CallTimeoutPolicy

SYNTH = "macro__macro_synthesize_shock_vector"
SCHEMA = "macro__macro_shock_schema"
CITES = "macro__macro_get_citations"
NARR = "arag__narrative_search"
COVERAGE = "arag__coverage"

DEFAULT_TIMEOUT_S = 600.0
DEFAULT_CALL_TIMEOUT_S = 90.0

_TOOL = st.text(alphabet="abcdefgh_", min_size=1, max_size=8).map(lambda s: "srv__" + s)
_SECONDS = st.floats(min_value=0.001, max_value=DEFAULT_TIMEOUT_S, allow_nan=False, allow_infinity=False)

# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_override_else_default_from_settings():
    # oracle: Requirement 1 by hand: SYNTH has an entry (540) -> 540; SCHEMA, CITES have none -> call_timeout_s
    # (300).
    s = {"timeout_s": 600, "call_timeout_s": 300, "tool_timeouts_s": {SYNTH: 540.0, NARR: 120}}
    p = CallTimeoutPolicy.from_settings(s)
    # exact: a lookup, no arithmetic
    assert p.timeout_for(SYNTH) == pytest.approx(540.0, abs=0)
    assert p.timeout_for(NARR) == pytest.approx(120.0, abs=0)
    assert p.timeout_for(SCHEMA) == pytest.approx(300.0, abs=0)
    assert p.timeout_for(CITES) == pytest.approx(300.0, abs=0)
    assert p.timeout_for(COVERAGE) == pytest.approx(300.0, abs=0)


@pytest.mark.criterion("C1")
def test_c1_missing_keys_take_defaults():
    # oracle: Requirement 3 defaults by hand: no call_timeout_s -> 90; no tool_timeouts_s -> {} so every tool
    # gets the default; an override with call_timeout_s missing still wins for its own tool.
    p_empty = CallTimeoutPolicy.from_settings({})
    p_only_overrides = CallTimeoutPolicy.from_settings({"tool_timeouts_s": {SYNTH: 500}})
    p_only_call = CallTimeoutPolicy.from_settings({"call_timeout_s": 45})
    # exact: a lookup, no arithmetic
    assert p_empty.timeout_for(SYNTH) == pytest.approx(DEFAULT_CALL_TIMEOUT_S, abs=0)
    assert p_empty.timeout_for(SCHEMA) == pytest.approx(DEFAULT_CALL_TIMEOUT_S, abs=0)
    assert p_only_overrides.timeout_for(SYNTH) == pytest.approx(500.0, abs=0)
    assert p_only_overrides.timeout_for(SCHEMA) == pytest.approx(DEFAULT_CALL_TIMEOUT_S, abs=0)
    assert p_only_call.timeout_for(SYNTH) == pytest.approx(45.0, abs=0)


@pytest.mark.criterion("C1")
def test_c1_constructor_lookup():
    # oracle: Requirement 1 by hand on the constructor's own arguments: per_tool_s entry, else default_s.
    p = CallTimeoutPolicy(default_s=90.0, per_tool_s={SYNTH: 420.0}, degrade_after=1)
    # exact: a lookup, no arithmetic
    assert p.timeout_for(SYNTH) == pytest.approx(420.0, abs=0)
    assert p.timeout_for(SCHEMA) == pytest.approx(90.0, abs=0)


@pytest.mark.criterion("C1")
@given(default=_SECONDS, overrides=st.dictionaries(_TOOL, _SECONDS, max_size=6), probe=_TOOL)
def test_c1_property_lookup(default: float, overrides: dict[str, float], probe: str):
    # oracle: Requirement 1 restated: timeout_for(t) = tool_timeouts_s[t] if t in the map else call_timeout_s.
    s = {"timeout_s": DEFAULT_TIMEOUT_S, "call_timeout_s": default, "tool_timeouts_s": dict(overrides)}
    p = CallTimeoutPolicy.from_settings(s)
    for tool in [*overrides, probe]:
        expected = overrides.get(tool, default)
        # exact: a lookup, no arithmetic
        assert p.timeout_for(tool) == pytest.approx(expected, abs=0), tool


# ----------------------------------------------------------------------------------------- C2

_VALID = {"timeout_s": 600, "call_timeout_s": 300, "tool_timeouts_s": {SYNTH: 540}, "degrade_after_timeouts": 2}


def _names(*keys: str) -> str:
    """A regex matching an error message that names any of `keys` (Requirement 3: "naming the key")."""
    return "|".join(re.escape(k) for k in keys)


def _with(**changes: Any) -> dict[str, Any]:
    s = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _VALID.items()}
    s.update(changes)
    return s


# (settings, keys of which the error message must name at least one) — Requirement 3, case by case.
_INVALID_CASES = [
    (_with(timeout_s=0), ("timeout_s",)),
    (_with(timeout_s=-5.0), ("timeout_s",)),
    (_with(timeout_s=math.nan), ("timeout_s",)),
    (_with(timeout_s=math.inf), ("timeout_s",)),
    (_with(call_timeout_s=0), ("call_timeout_s",)),
    (_with(call_timeout_s=-1), ("call_timeout_s",)),
    (_with(call_timeout_s=math.nan), ("call_timeout_s",)),
    (_with(call_timeout_s=math.inf), ("call_timeout_s",)),
    (_with(call_timeout_s=-math.inf), ("call_timeout_s",)),
    (_with(tool_timeouts_s={SYNTH: 0}), ("tool_timeouts_s", SYNTH)),
    (_with(tool_timeouts_s={SYNTH: -10.0}), ("tool_timeouts_s", SYNTH)),
    (_with(tool_timeouts_s={SYNTH: math.nan}), ("tool_timeouts_s", SYNTH)),
    (_with(tool_timeouts_s={SYNTH: math.inf}), ("tool_timeouts_s", SYNTH)),
    (_with(call_timeout_s=600.5), ("call_timeout_s", "timeout_s")),
    (_with(tool_timeouts_s={SYNTH: 540, SCHEMA: 601}), ("tool_timeouts_s", SCHEMA, "timeout_s")),
    ({"call_timeout_s": 700}, ("call_timeout_s", "timeout_s")),  # default timeout_s is 600
    ({"timeout_s": 60}, ("call_timeout_s", "timeout_s")),  # default call_timeout_s 90 > 60
    (_with(degrade_after_timeouts=0), ("degrade_after_timeouts",)),
    (_with(degrade_after_timeouts=-3), ("degrade_after_timeouts",)),
    (_with(degrade_after_timeouts=1.5), ("degrade_after_timeouts",)),
    (_with(degrade_after_timeouts=True), ("degrade_after_timeouts",)),
]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("settings", "keys"), _INVALID_CASES)
def test_c2_invalid_settings_raise_naming_the_key(settings: dict[str, Any], keys: tuple[str, ...]):
    # oracle: Requirement 3 case by case (listed in _INVALID_CASES): each violates exactly one stated rule,
    # so from_settings must raise ValueError whose message names the offending key.
    with pytest.raises(ValueError, match=_names(*keys)):
        CallTimeoutPolicy.from_settings(settings)


@pytest.mark.criterion("C2")
def test_c2_call_timeout_equal_to_overall_is_valid():
    # oracle: Requirement 3 / C2: "A call timeout equal to timeout_s is valid"; Requirement 1 then gives the
    # lookups by hand.
    p = CallTimeoutPolicy.from_settings(
        {"timeout_s": 300, "call_timeout_s": 300, "tool_timeouts_s": {SYNTH: 300}, "degrade_after_timeouts": 1})
    # exact: a lookup, no arithmetic
    assert p.timeout_for(SYNTH) == pytest.approx(300.0, abs=0)
    assert p.timeout_for(SCHEMA) == pytest.approx(300.0, abs=0)


_BAD_TIMEOUT = st.one_of(
    st.sampled_from([0.0, -0.0, math.nan, math.inf, -math.inf]),
    st.floats(max_value=0.0, allow_nan=False, allow_infinity=False),
)


@pytest.mark.criterion("C2")
@given(bad=_BAD_TIMEOUT, where=st.sampled_from(["timeout_s", "call_timeout_s", "tool_timeouts_s"]))
def test_c2_property_non_positive_or_non_finite_timeout_raises(bad: float, where: str):
    # oracle: Requirement 3: every timeout must be a finite number > 0, wherever it sits.
    s = _with(tool_timeouts_s={SYNTH: bad}) if where == "tool_timeouts_s" else _with(**{where: bad})
    with pytest.raises(ValueError, match=_names(where, SYNTH)):
        CallTimeoutPolicy.from_settings(s)


@pytest.mark.criterion("C2")
@given(overall=st.floats(min_value=1.0, max_value=1e5), excess=st.floats(min_value=1e-3, max_value=1e5),
       per_tool=st.booleans())
def test_c2_property_call_timeout_vs_overall_boundary(overall: float, excess: float, per_tool: bool):
    # oracle: Requirement 3 metamorphic pair: the same settings are valid with the call timeout equal to
    # timeout_s and invalid once it exceeds timeout_s by any positive amount.
    above = overall + excess
    assume(above > overall)
    key = "tool_timeouts_s" if per_tool else "call_timeout_s"

    def settings(call: float) -> dict[str, Any]:
        if per_tool:
            return {"timeout_s": overall, "call_timeout_s": overall, "tool_timeouts_s": {SYNTH: call}}
        return {"timeout_s": overall, "call_timeout_s": call}

    p = CallTimeoutPolicy.from_settings(settings(overall))
    # exact: a lookup, no arithmetic
    assert p.timeout_for(SYNTH) == pytest.approx(overall, abs=0)
    with pytest.raises(ValueError, match=_names(key, SYNTH, "timeout_s")):
        CallTimeoutPolicy.from_settings(settings(above))


@pytest.mark.criterion("C2")
@given(n=st.integers(min_value=-50, max_value=50))
def test_c2_property_degrade_after_integer_threshold(n: int):
    # oracle: Requirement 3: degrade_after_timeouts must be an integer >= 1. Valid n is observed through
    # Requirement 2 (degraded exactly at the n-th recorded timeout); invalid n raises.
    if n < 1:
        with pytest.raises(ValueError, match="degrade_after_timeouts"):
            CallTimeoutPolicy.from_settings({"degrade_after_timeouts": n})
        return
    p = CallTimeoutPolicy.from_settings({"degrade_after_timeouts": n})
    for _ in range(n - 1):
        p.record_timeout(SYNTH)
    assert p.is_degraded(SYNTH) is False
    p.record_timeout(SYNTH)
    assert p.is_degraded(SYNTH) is True


# ----------------------------------------------------------------------------------------- C3


@pytest.mark.criterion("C3")
def test_c3_degrades_on_nth_timeout_only_that_tool():
    # oracle: Requirement 2 by hand with n = 3: not degraded after 0, 1, 2 timeouts; degraded after 3 and 4;
    # SCHEMA (same server, never timed out) and NARR stay usable throughout.
    p = CallTimeoutPolicy(default_s=90.0, per_tool_s={}, degrade_after=3)
    for k in range(5):
        assert p.is_degraded(SYNTH) is (k >= 3), k
        assert p.is_degraded(SCHEMA) is False
        assert p.is_degraded(NARR) is False
        p.record_timeout(SYNTH)
    assert p.is_degraded(SYNTH) is True
    assert p.is_degraded(SCHEMA) is False


@pytest.mark.criterion("C3")
def test_c3_default_degrade_after_is_one():
    # oracle: Requirement 3 default degrade_after_timeouts = 1 with Requirement 2: one timeout degrades that
    # tool and no other.
    p = CallTimeoutPolicy.from_settings({"timeout_s": 600, "call_timeout_s": 300})
    assert p.is_degraded(SYNTH) is False
    p.record_timeout(SYNTH)
    assert p.is_degraded(SYNTH) is True
    assert p.is_degraded(SCHEMA) is False
    assert p.is_degraded(CITES) is False


@pytest.mark.criterion("C3")
@given(n=st.integers(min_value=1, max_value=5),
       events=st.lists(st.sampled_from([SYNTH, SCHEMA, CITES, NARR, COVERAGE]), max_size=25))
def test_c3_property_degraded_iff_count_reaches_n(n: int, events: list[str]):
    # oracle: Requirement 2 restated: after any sequence of record_timeout calls, is_degraded(t) is true
    # exactly when t has been recorded at least n times; other tools' records don't count.
    p = CallTimeoutPolicy(default_s=90.0, per_tool_s={}, degrade_after=n)
    counts = dict.fromkeys([SYNTH, SCHEMA, CITES, NARR, COVERAGE], 0)
    for tool in events:
        p.record_timeout(tool)
        counts[tool] += 1
        for t, c in counts.items():
            assert p.is_degraded(t) is (c >= n), (t, c, n)
    # the final state is checked even for an empty event list
    for t, c in counts.items():
        assert p.is_degraded(t) is (c >= n), (t, c, n)
