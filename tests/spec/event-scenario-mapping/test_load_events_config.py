"""event-scenario-mapping C1: ``load_events_config`` parses and validates ``events.json``.

Oracle: Requirement 1 by hand: weights as given, ``excluded`` and ``trigger_names`` upper-cased, the two
crowding integers as given, a theme per scenario id (the empty theme for ids without one), missing
theme lists empty and missing theme bools false; ``ConfigError`` for each listed invalid input.
"""

from __future__ import annotations

import copy
import math
from typing import Any, NamedTuple

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.common.config.models import ConfigError
from src.scenariogen.events import load_events_config


class ScenarioTheme(NamedTuple):
    """Expected theme (the spec's ScenarioTheme fields); the stub class has no fields yet."""

    news_items: frozenset[str]
    tone: bool
    crowding: bool
    trigger_items: frozenset[str]
    trigger_names: frozenset[str]


class EventsConfig(NamedTuple):
    """Expected config (the spec's EventsConfig fields); compared field by field with the result."""

    item_weights: dict[str, float]
    excluded: frozenset[str]
    crowding_min_obs: int
    crowding_max_staleness_days: int
    themes: dict[str, ScenarioTheme]

SCENARIOS = ["ai_deflation", "crowded_unwind", "rate_capital"]

RAW: dict[str, Any] = {
    "description": "synthetic events config",
    "_comment": "ignored",
    "item_weights": {"2.02": 0.6, "5.02": 1.0, "7.01": 0.2, "8.01": 0},
    "excluded": ["xlk", "SMH", "Qqq"],
    "crowding_min_obs": 24,
    "crowding_max_staleness_days": 30,
    "themes": {
        "ai_deflation": {"news_items": ["2.02", "7.01"], "tone": True, "crowding": True,
                         "trigger_items": ["2.02"], "trigger_names": ["googl", "META"], "_why": "x"},
        "crowded_unwind": {"crowding": True},
    },
}

EMPTY = ScenarioTheme(frozenset(), False, False, frozenset(), frozenset())

# Hand-built from Requirement 1 for RAW.
WANT = EventsConfig(
    item_weights={"2.02": 0.6, "5.02": 1.0, "7.01": 0.2, "8.01": 0.0},
    excluded=frozenset({"XLK", "SMH", "QQQ"}),
    crowding_min_obs=24,
    crowding_max_staleness_days=30,
    themes={
        "ai_deflation": ScenarioTheme(frozenset({"2.02", "7.01"}), True, True, frozenset({"2.02"}),
                                      frozenset({"GOOGL", "META"})),
        "crowded_unwind": ScenarioTheme(frozenset(), False, True, frozenset(), frozenset()),
        "rate_capital": EMPTY,
    },
)


def _check(got: Any, want: EventsConfig) -> None:
    assert dict(got.item_weights) == want.item_weights
    assert set(got.excluded) == set(want.excluded)
    assert got.crowding_min_obs == want.crowding_min_obs
    assert got.crowding_max_staleness_days == want.crowding_max_staleness_days
    assert set(got.themes) == set(want.themes)
    for sid, th in want.themes.items():
        g = got.themes[sid]
        assert set(g.news_items) == set(th.news_items), sid
        assert g.tone is th.tone, sid
        assert g.crowding is th.crowding, sid
        assert set(g.trigger_items) == set(th.trigger_items), sid
        assert set(g.trigger_names) == set(th.trigger_names), sid


@pytest.mark.criterion("C1")
def test_c1_point_parses_config():
    # oracle: Requirement 1 by hand (WANT above): weight 0 is allowed; "xlk"/"Qqq" -> "XLK"/"QQQ",
    #   "googl" -> "GOOGL"; crowded_unwind's missing lists are empty and missing tone is false;
    #   rate_capital has no theme -> the empty theme; "description" and "_"-keys are ignored.
    raw = copy.deepcopy(RAW)
    got = load_events_config(raw, SCENARIOS)
    _check(got, WANT)
    assert raw == RAW  # the parsed input is not mutated


@pytest.mark.criterion("C1")
def test_c1_point_boundary_integers_accepted():
    # oracle: Requirement 1 by hand: crowding_min_obs >= 1 and crowding_max_staleness_days >= 0 are
    #   valid, so 1 and 0 are accepted unchanged; no themes -> every scenario id gets the empty theme.
    raw = {"item_weights": {"5.02": 1.0}, "crowding_min_obs": 1, "crowding_max_staleness_days": 0}
    got = load_events_config(raw, ["keyman_nvda", "tariff_shock"])
    want = EventsConfig({"5.02": 1.0}, frozenset(), 1, 0, {"keyman_nvda": EMPTY, "tariff_shock": EMPTY})
    _check(got, want)


def _set(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    raw = copy.deepcopy(RAW)
    node = raw
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    return raw


def _drop(key: str) -> dict[str, Any]:
    raw = copy.deepcopy(RAW)
    del raw[key]
    return raw


INVALID = {
    "unknown top-level key": _set(("item_weight",), {"2.02": 0.6}),
    "unknown theme key": _set(("themes", "ai_deflation", "news_item"), ["2.02"]),
    "theme for unknown scenario": _set(("themes", "not_a_scenario"), {"tone": True}),
    "news item not in item_weights": _set(("themes", "ai_deflation", "news_items"), ["2.02", "9.99"]),
    "trigger item not in item_weights": _set(("themes", "ai_deflation", "trigger_items"), ["4.02"]),
    "negative weight": _set(("item_weights", "7.01"), -0.2),
    "weight not a number": _set(("item_weights", "7.01"), "0.2x"),
    "infinite weight": _set(("item_weights", "7.01"), math.inf),
    "nan weight": _set(("item_weights", "7.01"), math.nan),
    "tone not a bool": _set(("themes", "ai_deflation", "tone"), "true"),
    "crowding not a bool": _set(("themes", "crowded_unwind", "crowding"), "yes"),
    "missing item_weights": _drop("item_weights"),
    "missing crowding_min_obs": _drop("crowding_min_obs"),
    "missing crowding_max_staleness_days": _drop("crowding_max_staleness_days"),
    "crowding_min_obs below 1": _set(("crowding_min_obs",), 0),
    "crowding_max_staleness_days below 0": _set(("crowding_max_staleness_days",), -1),
}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", sorted(INVALID))
def test_c1_point_invalid_raises_config_error(case):
    # oracle: Requirement 1's list of ConfigError cases, one invalid input per case (by hand).
    with pytest.raises(ConfigError):
        load_events_config(INVALID[case], SCENARIOS)


_comment_keys = st.text(alphabet="abcdefghij_", max_size=8).map(lambda s: "_" + s)
_comment_values = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=5),
                            st.lists(st.text(max_size=3), max_size=3))


@pytest.mark.criterion("C1")
@given(top=st.dictionaries(_comment_keys, _comment_values, max_size=3),
       in_theme=st.dictionaries(_comment_keys, _comment_values, max_size=3))
def test_c1_property_underscore_keys_ignored(top, in_theme):
    # oracle: Requirement 1 by hand: "_"-prefixed keys at the top level and in a theme are ignored, so the
    #   result is always the hand-built WANT.
    raw = copy.deepcopy(RAW)
    raw.update(top)
    raw["themes"]["ai_deflation"].update(in_theme)
    raw["themes"]["crowded_unwind"].update(in_theme)
    _check(load_events_config(raw, SCENARIOS), WANT)
