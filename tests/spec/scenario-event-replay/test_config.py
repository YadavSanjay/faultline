"""scenario-event-replay C1: ``load_event_replay_config`` / ``event_replay_settings`` validate the config.

Oracle: Requirement 1 by hand: the episodes in file order with ``as_of`` parsed to a ``date``;
``min_abs_z`` default 1.0 and ``sleeves`` default empty; ``description`` and ``_``-prefixed keys
ignored; ``ConfigError`` for every listed invalid input, from both functions.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.common.config.models import ConfigError
from src.scenariogen.event_replay import Episode, event_replay_settings, load_event_replay_config

Doc = dict[str, Any]


def _set(path: tuple[Any, ...], value: Any) -> Callable[[Doc], None]:
    """A change that sets ``doc[path[0]]...[path[-1]] = value``."""
    def change(doc: Doc) -> None:
        node: Any = doc
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = value
    return change


def _drop(path: tuple[Any, ...]) -> Callable[[Doc], None]:
    """A change that deletes ``doc[path[0]]...[path[-1]]``."""
    def change(doc: Doc) -> None:
        node: Any = doc
        for step in path[:-1]:
            node = node[step]
        del node[path[-1]]
    return change


# Every invalid input of Requirement 1, each a single change to the valid document (conftest _RAW).
INVALID: list[tuple[str, Callable[[Doc], None]]] = [
    ("unknown top-level key", _set(("episode_list",), [])),
    ("unknown episode key", _set(("episodes", 0, "horizon"), 18)),
    ("missing episodes", _drop(("episodes",))),
    ("empty episodes", _set(("episodes",), [])),
    ("episodes not a list", _set(("episodes",), {})),
    ("episode not an object", _set(("episodes",), ["yen_carry"])),
    ("duplicate name", _set(("episodes", 2, "name"), "yen_carry")),
    ("empty name", _set(("episodes", 1, "name"), "")),
    ("missing name", _drop(("episodes", 1, "name"))),
    *[(f"bad as_of {bad!r}", _set(("episodes", 0, "as_of"), bad))
      for bad in ["2024/07/10", "10-07-2024", "2024-13-01", "2024-02-30", "not-a-date", "", 20240710, None]],
    ("missing as_of", _drop(("episodes", 0, "as_of"))),
    *[(f"bad horizon_days {bad!r}", _set(("episodes", 0, "horizon_days"), bad))
      for bad in [0, -3, 1.5, "18", True, False, None]],
    ("missing horizon_days", _drop(("episodes", 0, "horizon_days"))),
    *[(f"bad min_abs_z {bad!r}", _set(("min_abs_z",), bad)) for bad in [0, 0.0, -0.5, "1.0", None, [1.0]]],
    ("empty sleeve name", _set(("sleeves", "eq:AMD"), "")),
    ("non-string sleeve name", _set(("sleeves", "eq:AMD"), 3)),
    ("null sleeve name", _set(("sleeves", "eq:AMD"), None)),
    ("empty factor key", _set(("sleeves", ""), "semis")),
    ("sleeves not an object", _set(("sleeves",), ["semis"])),
]

_IGNORED_VALUES = st.recursive(st.none() | st.booleans() | st.integers() | st.text(max_size=8),
                               lambda inner: st.lists(inner, max_size=3), max_leaves=6)
_IGNORED_KEYS = st.dictionaries(st.text(max_size=8).map(lambda s: "_" + s), _IGNORED_VALUES, max_size=4)


@pytest.mark.criterion("C1")
def test_c1_point_valid_document_episodes_in_file_order(h):
    # oracle: Requirement 1 by hand: three episodes in file order (yen_carry, deepseek, tariff, not
    #   sorted), as_of parsed to date, horizon_days as given; description and _comment ignored.
    result = load_event_replay_config(h.raw())
    assert isinstance(result, list)
    assert [tuple(e) for e in result] == [tuple(e) for e in h.WANT_EPISODES]
    assert all(isinstance(e, Episode) for e in result)
    assert [e.name for e in result] == ["yen_carry", "deepseek", "tariff"]
    assert [e.as_of for e in result] == [date(2024, 7, 10), date(2025, 1, 24), date(2025, 4, 2)]
    assert all(type(e.as_of) is date for e in result)
    assert [e.horizon_days for e in result] == [18, 1, 4]


@pytest.mark.criterion("C1")
def test_c1_point_valid_document_settings(h):
    # oracle: Requirement 1 by hand: min_abs_z 1.5 and the four sleeve entries as given.
    min_abs_z, sleeves = event_replay_settings(h.raw())
    assert min_abs_z == pytest.approx(1.5, abs=0.0)  # exact: copied from the document
    assert sleeves == {"eq:NVDA": "semis", "eq:MSFT": "platforms", "ir:USD_SOFR_10Y": "rates",
                       "credit:HY": "credit"}


@pytest.mark.criterion("C1")
def test_c1_point_defaults_when_optional_keys_absent(h):
    # oracle: Requirement 1: without min_abs_z and sleeves the settings are (1.0, {}); the single
    #   episode is deepseek 2025-01-24 horizon 1.
    doc = h.minimal()
    assert [tuple(e) for e in load_event_replay_config(doc)] == [("deepseek", date(2025, 1, 24), 1)]
    min_abs_z, sleeves = event_replay_settings(doc)
    assert min_abs_z == pytest.approx(1.0, abs=0.0)  # exact: the documented default
    assert sleeves == {}


@pytest.mark.criterion("C1")
def test_c1_point_integer_min_abs_z_is_a_number(h):
    # oracle: Requirement 1: min_abs_z is "number > 0", so the integer 2 is valid and equals 2.0.
    doc = h.minimal()
    doc["min_abs_z"] = 2
    min_abs_z, sleeves = event_replay_settings(doc)
    assert min_abs_z == pytest.approx(2.0, abs=0.0)  # exact: copied from the document
    assert sleeves == {}


@pytest.mark.criterion("C1")
def test_c1_point_underscore_keys_in_episode_are_ignored(h):
    # oracle: Requirement 1 "_-prefixed keys are ignored": a _why inside an episode is not an unknown
    #   key; the episodes are unchanged.
    doc = h.raw()
    doc["episodes"][0]["_why"] = "BoJ hike and carry unwind"
    assert [tuple(e) for e in load_event_replay_config(doc)] == [tuple(e) for e in h.WANT_EPISODES]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("label", "change"), INVALID, ids=[c[0] for c in INVALID])
def test_c1_point_invalid_input_raises_config_error_from_loader(h, label, change):
    # oracle: Requirement 1's list of invalid inputs; each case is one change to the valid document.
    doc = h.raw()
    change(doc)
    with pytest.raises(ConfigError, match=r"\S"):
        load_event_replay_config(doc)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("label", "change"), INVALID, ids=[c[0] for c in INVALID])
def test_c1_point_invalid_input_raises_config_error_from_settings(h, label, change):
    # oracle: Requirement 1 / C1: event_replay_settings reads "the same validated document", so it
    #   raises ConfigError for the same invalid inputs.
    doc = h.raw()
    change(doc)
    with pytest.raises(ConfigError, match=r"\S"):
        event_replay_settings(doc)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(top=_IGNORED_KEYS, per_episode=st.lists(_IGNORED_KEYS, min_size=3, max_size=3))
def test_c1_property_underscore_keys_never_change_result(h, top, per_episode):
    # oracle: C1 property: adding _-prefixed keys (top level and inside episodes) leaves both results
    #   equal to the hand-listed episodes and settings of Requirement 1.
    doc = h.raw()
    doc.update(top)
    for episode, extra in zip(doc["episodes"], per_episode, strict=True):
        episode.update(extra)
    assert [tuple(e) for e in load_event_replay_config(doc)] == [tuple(e) for e in h.WANT_EPISODES]
    min_abs_z, sleeves = event_replay_settings(doc)
    assert min_abs_z == pytest.approx(h.WANT_SETTINGS[0], abs=0.0)  # exact: copied from the document
    assert sleeves == h.WANT_SETTINGS[1]
