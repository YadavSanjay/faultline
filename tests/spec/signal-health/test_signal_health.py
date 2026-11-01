"""signal-health C2: ``signal_health.signal_health`` rolls per-source statuses up into one block.

Oracle: Requirement 2 by hand: ``overall`` = worst non-unused status by down > degraded > ok, "unused"
when there is none; ``counts`` has ok/degraded/down/unused always; ``sources`` ordered down, degraded,
ok, unused, then by ``source`` name; ValueError for a duplicate ``source``. Inputs are hand-written
status dicts (conftest.status_dict), never produced by ``source_status``.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.signal_health import signal_health

ORDER = {"down": 0, "degraded": 1, "ok": 2, "unused": 3}
STATUS = st.sampled_from(["ok", "degraded", "down", "unused"])
NAMES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=10)


@st.composite
def _pairs(draw):
    """Distinct source names, each with a status."""
    names = draw(st.lists(NAMES, unique=True, max_size=10))
    return [(n, draw(STATUS)) for n in names]


@pytest.mark.criterion("C2")
def test_c2_point_mixed_rollup(h):
    # oracle: Requirement 2 by hand (conftest.mixed_rollup): two sources of each status -> overall
    #   "down", counts 2/2/2/2, sources down (macrorag, xbrlrag), degraded (gpr, options), ok (crowding,
    #   news), unused (aaa_extra, throughput) -- by status first, so "aaa_extra" sorts last.
    assert signal_health(h.mixed_statuses()) == h.mixed_rollup()


@pytest.mark.criterion("C2")
def test_c2_point_worst_is_degraded_when_nothing_down(h):
    # oracle: Requirement 2 by hand: ok + degraded + unused -> overall "degraded"; down count 0 still
    #   present; order degraded (options), ok (news), unused (gpr).
    statuses = [h.status_dict("news", "ok"), h.status_dict("gpr", "unused"), h.status_dict("options", "degraded")]
    assert signal_health(statuses) == {
        "overall": "degraded",
        "counts": {"ok": 1, "degraded": 1, "down": 0, "unused": 1},
        "sources": [h.status_dict("options", "degraded"), h.status_dict("news", "ok"),
                    h.status_dict("gpr", "unused")],
    }


@pytest.mark.criterion("C2")
def test_c2_point_ok_and_unused_rolls_up_to_ok(h):
    # oracle: Requirement 2 by hand: unused is ignored for overall -> "ok"; ok sources by name
    #   (crowding before news), then unused.
    statuses = [h.status_dict("throughput", "unused"), h.status_dict("news", "ok"), h.status_dict("crowding", "ok")]
    assert signal_health(statuses) == {
        "overall": "ok",
        "counts": {"ok": 2, "degraded": 0, "down": 0, "unused": 1},
        "sources": [h.status_dict("crowding", "ok"), h.status_dict("news", "ok"),
                    h.status_dict("throughput", "unused")],
    }


@pytest.mark.criterion("C2")
def test_c2_point_all_unused(h):
    # oracle: Requirement 2 by hand: every status unused -> overall "unused"; counts unused 2, the other
    #   three keys 0; sources by name (gpr, throughput).
    statuses = [h.status_dict("throughput", "unused"), h.status_dict("gpr", "unused")]
    assert signal_health(statuses) == {
        "overall": "unused",
        "counts": {"ok": 0, "degraded": 0, "down": 0, "unused": 2},
        "sources": [h.status_dict("gpr", "unused"), h.status_dict("throughput", "unused")],
    }


@pytest.mark.criterion("C2")
def test_c2_point_empty_list():
    # oracle: Requirement 2 by hand: no statuses -> overall "unused", all four counts present and 0,
    #   sources [].
    assert signal_health([]) == {
        "overall": "unused",
        "counts": {"ok": 0, "degraded": 0, "down": 0, "unused": 0},
        "sources": [],
    }


@pytest.mark.criterion("C2")
def test_c2_point_duplicate_source_raises(h):
    # oracle: Requirement 2 "It raises ValueError for two statuses with the same source" -- even when
    #   the two duplicates carry different statuses.
    statuses = [h.status_dict("news", "ok"), h.status_dict("gpr", "unused"), h.status_dict("news", "down")]
    with pytest.raises(ValueError, match=r"\S"):
        signal_health(statuses)


@pytest.mark.criterion("C2")
def test_c2_point_input_not_mutated(h):
    # oracle: Requirement 2 returns a new block; the caller's list (order included) equals a deepcopy
    #   taken before the call.
    statuses = h.mixed_statuses()
    before = copy.deepcopy(statuses)
    got = signal_health(statuses)
    assert statuses == before
    assert got == h.mixed_rollup()


@pytest.mark.criterion("C2")
@given(pairs=_pairs())
def test_c2_property_counts_overall_and_order(pairs, h):
    # oracle: Requirement 2: the four count keys are always present and sum to len(statuses), each equal
    #   to the number of inputs with that status; overall is the min-rank non-unused status (down >
    #   degraded > ok) else "unused"; sources is the input sorted by (status rank, source name).
    statuses = [h.status_dict(n, s) for n, s in pairs]
    got = signal_health(statuses)

    assert set(got["counts"]) == {"ok", "degraded", "down", "unused"}
    assert sum(got["counts"].values()) == len(statuses)
    for s in ORDER:
        assert got["counts"][s] == sum(1 for _, x in pairs if x == s)

    used = [s for _, s in pairs if s != "unused"]
    assert got["overall"] == (min(used, key=ORDER.__getitem__) if used else "unused")

    assert got["sources"] == sorted(statuses, key=lambda d: (ORDER[d["status"]], d["source"]))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(pairs=_pairs(), seed=st.randoms(use_true_random=False))
def test_c2_metamorphic_shuffling_input_never_changes_output(pairs, seed, h):
    # oracle: Requirement 2 metamorphic: the roll-up depends only on the set of statuses, so a shuffled
    #   copy of the input gives an identical result, and both equal the input sorted by (status rank,
    #   source name).
    statuses = [h.status_dict(n, s) for n, s in pairs]
    shuffled = list(statuses)
    seed.shuffle(shuffled)
    got = signal_health(shuffled)
    assert got == signal_health(statuses)
    assert got["sources"] == sorted(statuses, key=lambda d: (ORDER[d["status"]], d["source"]))


@pytest.mark.criterion("C2")
@given(pairs=_pairs().filter(bool), dup_status=STATUS, data=st.data())
def test_c2_property_any_duplicate_source_raises(pairs, dup_status, data, h):
    # oracle: Requirement 2 refusal: appending a second status for any existing source name raises,
    #   whatever its status.
    statuses = [h.status_dict(n, s) for n, s in pairs]
    name = data.draw(st.sampled_from([n for n, _ in pairs]))
    with pytest.raises(ValueError, match=r"\S"):
        signal_health([*statuses, h.status_dict(name, dup_status)])
