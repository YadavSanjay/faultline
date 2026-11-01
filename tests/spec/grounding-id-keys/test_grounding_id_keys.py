"""grounding-id-keys C1-C2: only chunk and fact ids count as citation ids.

Every expected value is the spec's own definition (docs/specs/grounding-id-keys.md, Requirement) applied
by hand to JSON documents and scenarios built in the test: ids are the strings under ``chunk_id`` /
``fact_id`` and the string elements of ``chunk_ids`` / ``fact_ids`` lists, at any depth; a value under
``id`` (ARag plan-step ids such as ``"api_screen"`` or ``"Revenue-2025"``) is not an id.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.grounding import GroundingVerdict, assess_grounding, collect_chunk_ids
from src.scenariogen.llm.schema import CalibratedScenario, Citation, DriverLeg

# ----------------------------------------------------------------------------------------- shared

# An ARag facts response (spec C1 shape): a plan whose steps carry `id`, next to rows whose citation
# carries `fact_id`; the plan-step ids are not evidence.
ARAG_FACTS = {
    "plan": {"id": "plan-7", "steps": [
        {"id": "Revenue-2025", "op": "lookup", "concept": "Revenue", "period": "FY2025"},
        {"id": "api_screen", "op": "screen", "depends_on": ["Revenue-2025"]},
    ]},
    "rows": [
        {"concept": "Revenue", "value": 1.5e9, "step": {"id": "Revenue-2025"},
         "citation": {"fact_id": "fact-001", "accession": "0001045810-26-000010"}},
        {"concept": "NetIncome", "value": 2.0e8, "citation": {"fact_id": "fact-002"}},
    ],
}
ARAG_NARRATIVE = {
    "id": "narr-run-3",
    "chunks": [
        {"id": "row-0", "chunk_id": "0b7e2c1a-1111-4c2d-9e1f-aaaaaaaaaaaa", "text": "Export controls."},
        {"id": "row-1", "chunk_id": "5d0c9f3e-2222-4b8a-8c7d-bbbbbbbbbbbb", "text": "Supply chain."},
    ],
}


def _synth() -> dict[str, Any]:
    """The grounded MacroRAG synthesize response given in the spec Notes (no other gate rule fires)."""
    return {
        "shock_vector": {"USDTWD": {"value": 8.0, "unit": "pct", "chunk_ids": ["c1"]}},
        "world_view_id": "macro-2026-10-01",
        "chunks": [{"chunk_id": "c1"}],
        "synthesis_diagnostics": {"coverage_gap": False},
    }


def _scen(legs: list[tuple[str, list[str]]]) -> CalibratedScenario:
    """legs: (factor, cited ids); every leg is cited and there is no verification block (spec Notes)."""
    return CalibratedScenario(
        name="spec_scenario",
        legs=[DriverLeg(factor=f, move=-0.05, origin="equity" if f.startswith("eq:") else "macro",
                        citations=[Citation(chunk_id=c, source="macro") for c in cites]) for f, cites in legs],
        verification=None,
    )


def _seen(outputs: list[Any]) -> frozenset[str]:
    """seen_ids as the spec's C2 builds it: the union of collect_chunk_ids over the run's tool outputs."""
    seen: frozenset[str] = frozenset()
    for out in outputs:
        seen |= collect_chunk_ids(json.dumps(out))
    return seen


# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_arag_plan_step_ids_are_not_collected():
    # oracle: Requirement definition applied by hand: rows[].citation.fact_id -> fact-001, fact-002; every
    # `id` value (plan-7, Revenue-2025, api_screen) is excluded, and so are doc_id/accession/depends_on.
    assert collect_chunk_ids(json.dumps(ARAG_FACTS)) == frozenset({"fact-001", "fact-002"})


@pytest.mark.criterion("C1")
def test_c1_id_next_to_chunk_id_at_every_depth():
    # oracle: Requirement definition applied by hand: chunks[].chunk_id are the only ids; the sibling
    # `id` keys at depth 0 and depth 2 (narr-run-3, row-0, row-1) are not.
    assert collect_chunk_ids(json.dumps(ARAG_NARRATIVE)) == frozenset(
        {"0b7e2c1a-1111-4c2d-9e1f-aaaaaaaaaaaa", "5d0c9f3e-2222-4b8a-8c7d-bbbbbbbbbbbb"})


@pytest.mark.criterion("C1")
def test_c1_mixed_document_lists_and_id_only_document():
    # oracle: Requirement definition applied by hand: chunk_ids list -> k-1, k-2; fact_ids list -> f-1;
    # chunk_id -> c-9; the `id` values (top, i-1, i-2) are dropped. A document whose only string-valued
    # key is `id` (at any depth) has no ids -> empty set.
    doc = [
        {"id": "top", "chunk_ids": ["k-1", "k-2"]},
        [{"id": "i-1", "nested": {"fact_ids": ["f-1"], "inner": [{"id": "i-2", "chunk_id": "c-9"}]}}],
    ]
    assert collect_chunk_ids(json.dumps(doc)) == frozenset({"k-1", "k-2", "f-1", "c-9"})
    assert collect_chunk_ids(json.dumps({"id": "api_screen", "steps": [{"id": "Revenue-2025"}]})) == frozenset()


_GOOD = st.text(alphabet="abcdef0123456789-", min_size=1, max_size=10).map(lambda s: "good-" + s)
_BAD = st.text(alphabet="abcdef0123456789-_", min_size=1, max_size=10).map(lambda s: "plan-" + s)


@st.composite
def _tree(draw: Any, depth: int = 0) -> tuple[Any, set[str], set[str]]:
    """A JSON object with good ids under the four id keys and bad ids under `id`; returns (obj, good, bad)."""
    good: set[str] = set()
    bad: set[str] = set()
    obj: dict[str, Any] = {}
    kinds = ["good_key", "good_list", "bad_key", "obj", "list"] if depth < 3 else ["good_key", "good_list", "bad_key"]
    for j in range(draw(st.integers(0, 5))):
        kind = draw(st.sampled_from(kinds))
        if kind == "good_key":
            key = draw(st.sampled_from(["chunk_id", "fact_id"]))
            if key in obj:
                continue
            obj[key] = draw(_GOOD)
            good.add(obj[key])
        elif kind == "good_list":
            key = draw(st.sampled_from(["chunk_ids", "fact_ids"]))
            if key in obj:
                continue
            obj[key] = draw(st.lists(_GOOD, max_size=3))
            good.update(obj[key])
        elif kind == "bad_key":
            if "id" in obj:
                continue
            obj["id"] = draw(_BAD)
            bad.add(obj["id"])
        elif kind == "obj":
            child, g, b = draw(_tree(depth + 1))
            obj[f"nested_{j}"] = child
            good |= g
            bad |= b
        else:
            children = [draw(_tree(depth + 1)) for _ in range(draw(st.integers(0, 2)))]
            obj[f"items_{j}"] = [c for c, _, _ in children]
            for _, g, b in children:
                good |= g
                bad |= b
    return obj, good, bad


@pytest.mark.criterion("C1")
@given(tree=_tree(), anchor=_GOOD, plan_id=_BAD, indent=st.sampled_from([None, 2]), sort_keys=st.booleans())
def test_c1_property_id_values_never_collected(tree, anchor, plan_id, indent, sort_keys):
    # oracle: Requirement definition: good ids (planted under chunk_id/fact_id/chunk_ids/fact_ids) and bad
    # ids (planted under `id`, disjoint prefix) are known by construction; the result is exactly the good
    # set. Metamorphic: removing every `id` key leaves the result unchanged.
    obj, good, _bad = tree
    doc = {"id": plan_id, "chunk_id": anchor, "payload": obj}
    result = collect_chunk_ids(json.dumps(doc, indent=indent, sort_keys=sort_keys))
    assert result == frozenset(good | {anchor})

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "id"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    assert collect_chunk_ids(json.dumps(strip(doc))) == result


# ----------------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
def test_c2_leg_citing_a_plan_step_id_is_unseen():
    # oracle: Requirement + llm-grounding-gate Requirement 3 by hand: seen ids from the synthesis and the
    # ARag facts output are {c1, fact-001, fact-002}; "api_screen" appears only under `id`, so the leg
    # citing it trips unseen_citation, and the grounded synthesis trips nothing else.
    seen = _seen([_synth(), ARAG_FACTS, ARAG_NARRATIVE])
    scen = _scen([("fx:TWD", ["c1"]), ("eq:NVDA", ["api_screen"])])
    verdict = assess_grounding(scen, synthesis=_synth(), seen_ids=seen)
    assert isinstance(verdict, GroundingVerdict)
    assert verdict.ok is False
    assert verdict.reasons == ("unseen_citation",)


@pytest.mark.criterion("C2")
def test_c2_revenue_step_id_unseen_but_fact_id_seen():
    # oracle: same rules by hand: citing "Revenue-2025" (only under `id`) -> unseen_citation; citing the
    # row's fact_id "fact-001" and a narrative chunk_id instead -> every citation seen, no reason.
    seen = _seen([_synth(), ARAG_FACTS, ARAG_NARRATIVE])
    bad = assess_grounding(_scen([("fx:TWD", ["c1"]), ("eq:NVDA", ["fact-001", "Revenue-2025"])]),
                           synthesis=_synth(), seen_ids=seen)
    assert bad.reasons == ("unseen_citation",)
    assert bad.ok is False
    good = assess_grounding(
        _scen([("fx:TWD", ["c1"]), ("eq:NVDA", ["fact-001", "0b7e2c1a-1111-4c2d-9e1f-aaaaaaaaaaaa"])]),
        synthesis=_synth(), seen_ids=seen)
    assert good.reasons == ()
    assert good.ok is True


@pytest.mark.criterion("C2")
@given(tree=_tree(), plan_id=_BAD, data=st.data())
def test_c2_property_id_only_citation_is_unseen(tree, plan_id, data):
    # oracle: Requirement + llm-grounding-gate Requirement 3: bad ids are carried only under `id` (disjoint
    # prefix from good ids and c1), so a leg citing one trips exactly unseen_citation. Metamorphic: citing
    # a good id from the same outputs instead clears the scenario.
    obj, good, bad = tree
    outputs = [_synth(), {"id": plan_id, "payload": obj}]
    seen = _seen(outputs)
    cited_bad = data.draw(st.sampled_from(sorted(bad | {plan_id})))
    verdict = assess_grounding(_scen([("fx:TWD", ["c1"]), ("eq:NVDA", [cited_bad])]),
                               synthesis=_synth(), seen_ids=seen)
    assert verdict.reasons == ("unseen_citation",)
    assert verdict.ok is False
    cited_good = data.draw(st.sampled_from(sorted(good | {"c1"})))
    fixed = assess_grounding(_scen([("fx:TWD", ["c1"]), ("eq:NVDA", [cited_good])]),
                             synthesis=_synth(), seen_ids=seen)
    assert fixed.reasons == ()
    assert fixed.ok is True
