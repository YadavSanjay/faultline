"""ablation-waterfall-view C1: ``report_views.select_report`` picks one report from a date's reports.

Oracle: Requirement 1 by hand: with ``run_id`` the report carrying it (else None); otherwise, among the
reports containing ``require`` (all when None), the latest ``generated_at`` (missing / None ranks below
every dated report), ties to the greatest ``run_id`` (missing = ""); None when none qualifies.
ValueError for a duplicate non-empty ``run_id`` or an invalid ISO ``generated_at``; input not mutated.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.report_views import select_report

MISSING = object()


def rep(run_id: object, generated_at: object, health: bool) -> dict[str, object]:
    out: dict[str, object] = {"results": []}
    if run_id is not MISSING:
        out["run_id"] = run_id
    if generated_at is not MISSING:
        out["generated_at"] = generated_at
    if health:
        out["signal_health"] = {"overall": "ok"}
    return out


A = rep("r-a", "2026-10-18T09:00:00", True)
B = rep("r-b", "2026-10-18T10:00:00", False)     # latest overall, no signal_health
C = rep("r-c", "2026-10-18T08:00:00", True)
D = rep("r-d", "2026-10-18T09:00:00", True)      # ties A on generated_at; "r-d" > "r-a"
E = rep("r-e", MISSING, True)                    # no generated_at
F = rep("r-f", None, True)                       # generated_at None


def mixed() -> list[dict[str, object]]:
    return copy.deepcopy([A, B, C, D, E, F])


@pytest.mark.criterion("C1")
def test_c1_point_latest_with_and_without_require():
    # oracle: Requirement 1 by hand: require="signal_health" leaves A, C, D, E, F; latest dated is
    #   09:00 (A and D tie) -> greatest run_id "r-d" wins (B, at 10:00, lacks the key). Without require
    #   B (10:00) is the latest of all.
    assert select_report(mixed(), require="signal_health") == D
    assert select_report(mixed()) == B


@pytest.mark.criterion("C1")
def test_c1_point_by_run_id_present_and_absent():
    # oracle: Requirement 1 by hand: run_id="r-c" -> C although it is the oldest dated and require is
    #   not checked for it; run_id="r-zz" matches nothing -> None.
    assert select_report(mixed(), run_id="r-c") == C
    assert select_report(mixed(), run_id="r-zz") is None


@pytest.mark.criterion("C1")
def test_c1_point_undated_reports_rank_below_dated_ones():
    # oracle: Requirement 1 by hand: E (no generated_at) and F (None) rank below C although "r-e" and
    #   "r-f" sort after "r-c"; between the two undated ones the greatest run_id "r-f" wins.
    assert select_report(copy.deepcopy([E, C, F])) == C
    assert select_report(copy.deepcopy([F, E])) == F


@pytest.mark.criterion("C1")
def test_c1_point_missing_run_id_counts_as_empty_string_in_a_tie():
    # oracle: Requirement 1 by hand: same generated_at, one report without run_id ("") and one with
    #   "a" -> "a" > "" wins; two reports without run_id are not duplicates.
    no_id = rep(MISSING, "2026-10-18T09:00:00", False)
    with_id = rep("a", "2026-10-18T09:00:00", False)
    assert select_report([copy.deepcopy(no_id), copy.deepcopy(with_id)]) == with_id
    assert select_report([copy.deepcopy(no_id), rep(MISSING, "2026-10-18T07:00:00", False)]) == no_id


@pytest.mark.criterion("C1")
def test_c1_point_nothing_qualifies_gives_none():
    # oracle: Requirement 1 by hand: an empty list -> None; no report carries "layer_cost" -> None;
    #   while the same list without require still yields its latest report (B).
    assert select_report([]) is None
    assert select_report(mixed(), require="layer_cost") is None
    assert select_report(mixed(), require=None) == B


@pytest.mark.criterion("C1")
def test_c1_point_duplicate_run_id_raises():
    # oracle: Requirement 1 by hand: two reports with the same non-empty run_id "r-a" -> ValueError.
    with pytest.raises(ValueError, match=r"\S"):
        select_report([*mixed(), rep("r-a", "2026-10-18T12:00:00", True)])


@pytest.mark.criterion("C1")
def test_c1_point_invalid_generated_at_raises():
    # oracle: Requirement 1 by hand: datetime.fromisoformat("yesterday") fails -> ValueError (the report
    #   qualifies and would otherwise be compared).
    with pytest.raises(ValueError, match=r"\S"):
        select_report([*mixed(), rep("r-x", "yesterday", True)], require="signal_health")


@pytest.mark.criterion("C1")
def test_c1_point_input_not_mutated():
    # oracle: Requirement 1 by hand: "The input is not mutated" -> the list equals a deep copy taken
    #   before the call, and the pick is still D.
    reports = mixed()
    before = copy.deepcopy(reports)
    assert select_report(reports, require="signal_health") == D
    assert reports == before


STAMPS = st.sampled_from([MISSING, None, "2026-10-17T23:00:00", "2026-10-18T09:00:00", "2026-10-18T09:00:00",
                          "2026-10-18T10:30:00"])


@st.composite
def _reports(draw):
    """Distinct run_ids, random stamps (ties likely), random signal_health; at least one carries it."""
    ids = draw(st.lists(st.sampled_from(["a", "b", "c", "d", "e", "f"]), unique=True, min_size=1, max_size=6))
    out = [rep(i, draw(STAMPS), draw(st.booleans())) for i in ids]
    k = draw(st.integers(min_value=0, max_value=len(out) - 1))
    out[k]["signal_health"] = {"overall": "degraded"}
    return out


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(reports=_reports(), data=st.data())
def test_c1_property_shuffle_invariant_and_result_carries_require(reports, data):
    # oracle: Requirement 1 by hand: the rule is a total order on (dated, generated_at, run_id) with
    #   distinct run_ids, so any permutation gives the same pick; at least one report carries
    #   "signal_health", so the pick is not None and contains it; without require the list is non-empty
    #   so a report is picked too.
    shuffled = data.draw(st.permutations(reports))
    picked = select_report(reports, require="signal_health")
    assert picked is not None
    assert "signal_health" in picked
    assert select_report(shuffled, require="signal_health") == picked
    any_pick = select_report(reports)
    assert any_pick is not None
    assert any_pick in reports
    assert select_report(shuffled) == any_pick


@pytest.mark.criterion("C1")
@given(reports=_reports())
def test_c1_property_pick_is_maximal_on_the_hand_rule(reports):
    # oracle: Requirement 1 by hand: no qualifying report beats the pick on (dated, generated_at,
    #   run_id); the stamps are fixed ISO strings, so lexicographic order on them is time order.
    picked = select_report(reports, require="signal_health")
    assert picked is not None

    def key(r):
        g = r.get("generated_at")
        return (g is not None, g or "", r.get("run_id") or "")

    assert all(key(r) <= key(picked) for r in reports if "signal_health" in r)
