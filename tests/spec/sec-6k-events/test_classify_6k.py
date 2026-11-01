"""sec-6k-events C1: a 6-K maps to the 8-K item of the first file-name rule that matches it.

Oracle: the TSM primary-document file names (copied from TSM's public EDGAR index) classified by hand
against the committed rules, written below in the order the spec's Requirement (3) gives them. Properties:
rules after the first match never change the result; appending a rule that matches nothing changes
nothing; prepending a catch-all routine rule makes every result ``None``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.news.sec_events_downloader import classify_6k

# The committed rules, in the spec's order: routine first, then 5.02, 2.02, 8.01, 2.01.
RULES: list[dict[str, str | None]] = [
    {"pattern": "revenue", "item": None},
    {"pattern": "monthend", "item": None},
    {"pattern": "dividend", "item": None},
    {"pattern": "fsx", "item": None},
    {"pattern": "agm", "item": None},
    {"pattern": "changeof", "item": "5.02"},
    {"pattern": r"^tsm-\d{8}x6k\.htm$", "item": "2.02"},
    {"pattern": "board", "item": "8.01"},
    {"pattern": "directorsresolut", "item": "8.01"},
    {"pattern": "tosell", "item": "2.01"},
    {"pattern": "acqui", "item": "2.01"},
]

# Hand classification of real TSM file names against RULES (first matching pattern noted).
EXPECTED: dict[str, str | None] = {
    "tsm-revenue20261008.htm": None,              # revenue -> routine
    "tsm-monthend6kx20260924.htm": None,          # monthend -> routine
    "tsm-dividendadjustmentx202.htm": None,       # dividend -> routine
    "tsm-fsx20260814x6k.htm": None,               # fsx -> routine (not ^tsm-<8 digits>x6k)
    "tsm-agmx20260604x6k.htm": None,              # agm -> routine
    "a20251105changeofnaceo.htm": "5.02",         # changeof
    "a20250925changeofazceoandd.htm": "5.02",     # changeof
    "tsm-20261015x6k.htm": "2.02",                # ^tsm-\d{8}x6k\.htm$
    "tsm-boardx20260811.htm": "8.01",             # board
    "a20260210directorsresoluti.htm": "8.01",     # directorsresolut
    "tsmctosell81ofvanguardinte.htm": "2.01",     # tosell
    "sonysemiconductorsolutions.htm": None,       # no rule matches
    "a20251125tsmcmediastatement.htm": None,      # no rule matches
}
NAMES = sorted(EXPECTED)
# Names some rule in RULES matches (every name but the two no-match ones).
MATCHED = [n for n in NAMES if n not in ("sonysemiconductorsolutions.htm", "a20251125tsmcmediastatement.htm")]

ITEMS = st.sampled_from([None, "2.01", "2.02", "5.02", "7.01", "8.01"])
VALID_PATTERNS = st.sampled_from([".*", "tsm", "x6k", "a2025", r"\d+", "htm$", "board", "revenue", "^a", "ceo"])


def _rule(pattern: str, item: str | None) -> dict[str, str | None]:
    return {"pattern": pattern, "item": item}


@pytest.mark.criterion("C1")
def test_committed_rules_classify_tsm_file_names() -> None:
    # oracle: hand classification of the real TSM file names in EXPECTED against the committed rules
    for name, item in EXPECTED.items():
        assert classify_6k(name, RULES) == item, name


@pytest.mark.criterion("C1")
def test_first_matching_rule_decides() -> None:
    # oracle: hand — tsm-fsx20260814x6k.htm contains both "x6k" and "fsx"; whichever rule comes first wins
    name = "tsm-fsx20260814x6k.htm"
    assert classify_6k(name, [_rule("x6k", "2.02"), _rule("fsx", None)]) == "2.02"
    assert classify_6k(name, [_rule("fsx", None), _rule("x6k", "2.02")]) is None
    # oracle: hand — a20251105changeofnaceo.htm matches both "changeof" and "ceo"
    assert classify_6k("a20251105changeofnaceo.htm", [_rule("ceo", "8.01"), _rule("changeof", "5.02")]) == "8.01"


@pytest.mark.criterion("C1")
def test_matching_is_case_insensitive() -> None:
    # oracle: hand — the spec matches case-insensitively, so case never changes the classification
    assert classify_6k("TSM-20261015X6K.HTM", RULES) == "2.02"
    assert classify_6k("TSM-BoardX20260811.htm", RULES) == "8.01"
    assert classify_6k("TSM-REVENUE20261008.HTM", [*RULES, _rule("TSM", "8.01")]) is None
    assert classify_6k("a20251105changeofnaceo.htm", [_rule("CHANGEOF", "5.02")]) == "5.02"


@pytest.mark.criterion("C1")
def test_no_matching_rule_gives_none() -> None:
    # oracle: hand — no committed pattern occurs in these names; a rule that does match maps them
    name = "sonysemiconductorsolutions.htm"
    assert classify_6k(name, RULES) is None
    assert classify_6k(name, [*RULES, _rule("sony", "2.01")]) == "2.01"
    assert classify_6k(name, []) is None
    assert classify_6k(name, [_rule("semiconductor", "8.01")]) == "8.01"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("pattern", ["[unclosed", "(?P<x", "*tsm", "board)"])
def test_invalid_regex_raises_value_error(pattern: str) -> None:
    # oracle: spec — an invalid regex raises ValueError (each pattern here fails re.compile)
    with pytest.raises(ValueError, match=r"."):  # any message: the spec fixes only the type
        classify_6k("tsm-boardx20260811.htm", [_rule(pattern, "8.01"), *RULES])


@pytest.mark.criterion("C1")
@given(name=st.sampled_from(MATCHED),
       tail=st.lists(st.builds(_rule, VALID_PATTERNS, ITEMS), max_size=6))
def test_rules_after_the_first_match_never_change_the_result(name: str, tail: list[dict[str, str | None]]) -> None:
    # oracle: property — every MATCHED name is decided inside RULES, so trailing rules are never consulted
    assert classify_6k(name, [*RULES, *tail]) == EXPECTED[name]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(name=st.sampled_from(NAMES), suffix=st.text(alphabet="abcxyz0123", max_size=8), item=ITEMS)
def test_appending_a_rule_that_matches_nothing_changes_nothing(name: str, suffix: str, item: str | None) -> None:
    # oracle: metamorphic — no file name starts with "#", so a "^#..." rule matches none of them
    assert classify_6k(name, [*RULES, _rule("^#" + suffix, item)]) == EXPECTED[name]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(name=st.sampled_from(NAMES), rules=st.lists(st.builds(_rule, VALID_PATTERNS, ITEMS), max_size=6))
def test_prepending_a_catch_all_routine_rule_makes_every_result_none(
        name: str, rules: list[dict[str, str | None]]) -> None:
    # oracle: metamorphic — ".*" matches every name, so a leading routine catch-all decides every name
    assert classify_6k(name, RULES) == EXPECTED[name]
    assert classify_6k(name, [_rule(".*", None), *RULES]) is None
    assert classify_6k(name, [_rule(".*", None), *rules]) is None
