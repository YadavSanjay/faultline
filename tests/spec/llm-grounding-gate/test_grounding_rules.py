"""llm-grounding-gate C1-C2: the pure helpers `collect_chunk_ids` and `assess_grounding`.

Every expected value is the spec's own definition (docs/specs/llm-grounding-gate.md, C1 and
Requirement 3) applied by hand to documents and scenarios built in the test. Response shapes follow
the spec Notes (MacroRAG synthesize, ARag narrative search, ARag facts).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.grounding import GroundingVerdict, assess_grounding, collect_chunk_ids
from src.scenariogen.llm.schema import CalibratedScenario, Citation, DriverLeg

REASONS = ("all_legs_unsupported", "coverage_gap", "missing_world_view_id", "uncited_leg", "unseen_citation")

# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_macrorag_synthesize_response():
    # oracle: C1 definition applied by hand: chunks[].chunk_id -> c1, c2; shock_vector.<field>.chunk_ids
    # -> c1, c3, c2. world_view_id, kept_fields and answer are not id keys.
    doc = {
        "answer": "Rates up, TWD down.",
        "chunks": [{"chunk_id": "c1", "text": "passage one"}, {"chunk_id": "c2", "text": "passage two"}],
        "shock_vector": {
            "USDTWD": {"value": 8.0, "unit": "pct", "chunk_ids": ["c1", "c3"], "grounding_mode": "direct"},
            "DGS10": {"value": 25.0, "unit": "bp", "chunk_ids": ["c2"], "grounding_mode": "direct"},
        },
        "world_view_id": "macro-2026-10-01",
        "synthesis_diagnostics": {"outcome": "grounded", "coverage_gap": False, "kept_fields": ["USDTWD", "DGS10"]},
    }
    assert collect_chunk_ids(json.dumps(doc)) == frozenset({"c1", "c2", "c3"})


@pytest.mark.criterion("C1")
def test_c1_arag_narrative_and_facts_responses():
    # oracle: C1 definition applied by hand: narrative chunks[].chunk_id; facts rows[].citation.fact_id;
    # `doc_id`/`accession` are not id keys.
    narrative = {"chunks": [
        {"chunk_id": "0b7e2c1a-1111-4c2d-9e1f-aaaaaaaaaaaa", "text": "Export controls.", "section_path": "Item 1A"},
        {"chunk_id": "5d0c9f3e-2222-4b8a-8c7d-bbbbbbbbbbbb", "text": "Supply chain.", "section_path": "Item 7"},
    ]}
    facts = {"rows": [
        {"concept": "Revenue", "value": 1.5e9, "doc_id": "doc-9",
         "citation": {"fact_id": "fact-001", "accession": "0001045810-26-000010"}},
        {"concept": "NetIncome", "value": 2.0e8,
         "citation": {"fact_id": "fact-002"}},
    ]}
    assert collect_chunk_ids(json.dumps(narrative)) == frozenset(
        {"0b7e2c1a-1111-4c2d-9e1f-aaaaaaaaaaaa", "5d0c9f3e-2222-4b8a-8c7d-bbbbbbbbbbbb"})
    assert collect_chunk_ids(json.dumps(facts)) == frozenset({"fact-001", "fact-002"})


@pytest.mark.criterion("C1")
def test_c1_lists_non_strings_top_level_array_and_invalid_json():
    # oracle: C1 definition applied by hand: only string values count (ints/None/bools under id keys or
    # in id lists are skipped); a top-level array is searched; text that is not valid JSON gives the empty
    # set, and so does valid JSON with no id keys.
    doc = [
        {"fact_ids": ["f-1", 7, None, "f-2"], "chunk_id": 12},
        [{"nested": {"chunk_ids": ["k-1"], "fact_id": True}}],
        "chunk_id",
    ]
    assert collect_chunk_ids(json.dumps(doc)) == frozenset({"f-1", "f-2", "k-1"})
    for bad in ("upstream error: synthesis unavailable", "", '{"chunk_id": "c1"', "chunk_id: c1",
                '"c1"', "{}", '{"world_view_id": "w", "chunk": "c1"}'):
        assert collect_chunk_ids(bad) == frozenset(), bad


_ID = st.text(alphabet="abcdef0123456789-", min_size=1, max_size=10).map(lambda s: "id-" + s)
_NOISE = st.text(alphabet="ghijklmnop ", min_size=0, max_size=10).map(lambda s: "nz-" + s)
_NOISE_KEYS = ("text", "name", "section_path", "source", "doc_id", "world_view_id", "chunk", "ids", "value")
_SCALAR = st.one_of(_NOISE, st.integers(-5, 5), st.booleans(), st.none(),
                    st.floats(allow_nan=False, allow_infinity=False))


@st.composite
def _tree(draw: Any, depth: int = 0) -> tuple[Any, set[str]]:
    """A JSON object with planted ids under the C1 keys, plus noise; returns (object, planted ids)."""
    planted: set[str] = set()
    obj: dict[str, Any] = {}
    for j in range(draw(st.integers(0, 4))):
        kind = draw(st.sampled_from(["id_key", "id_list", "noise", "obj", "list"] if depth < 3
                                    else ["id_key", "id_list", "noise"]))
        if kind == "id_key":
            key = draw(st.sampled_from(["chunk_id", "fact_id"]))
            if key in obj:
                continue
            obj[key] = draw(_ID)
            planted.add(obj[key])
        elif kind == "id_list":
            key = draw(st.sampled_from(["chunk_ids", "fact_ids"]))
            if key in obj:
                continue
            ids = draw(st.lists(_ID, max_size=3))
            obj[key] = ids + draw(st.lists(st.one_of(st.integers(0, 9), st.none()), max_size=2))
            planted.update(ids)
        elif kind == "noise":
            obj[f"{draw(st.sampled_from(_NOISE_KEYS))}_{j}"] = draw(_SCALAR)
        elif kind == "obj":
            child, ids = draw(_tree(depth + 1))
            obj[f"nested_{j}"] = child
            planted |= ids
        else:
            children = [draw(_tree(depth + 1)) for _ in range(draw(st.integers(0, 2)))]
            obj[f"items_{j}"] = [c for c, _ in children] + [draw(_SCALAR)]
            for _, ids in children:
                planted |= ids
    return obj, planted


@pytest.mark.criterion("C1")
@given(tree=_tree(), anchor=_ID, as_list=st.booleans(), indent=st.sampled_from([None, 2]),
       sort_keys=st.booleans())
def test_c1_property_planted_ids_are_recovered_exactly(tree, anchor, as_list, indent, sort_keys):
    # oracle: C1 definition: the planted id set is known by construction (every string put under an id
    # key or into an id list, nothing else). Metamorphic: serialisation (indent, key order, wrapping in
    # an array) does not change the result; prefixing non-JSON text makes it invalid -> empty set.
    obj, planted = tree
    doc: Any = {"chunk_id": anchor, "payload": obj}
    doc = [doc] if as_list else doc
    text = json.dumps(doc, indent=indent, sort_keys=sort_keys)
    assert collect_chunk_ids(text) == frozenset(planted | {anchor})
    assert collect_chunk_ids("tool error: " + text) == frozenset()


# ----------------------------------------------------------------------------------------- C2

SEEN = frozenset({"arag-1", "arag-2", "c1", "c2"})


def _synth(**overrides: Any) -> dict[str, Any]:
    """A grounded MacroRAG synthesize response (spec Notes shape); overrides replace/delete keys."""
    body: dict[str, Any] = {
        "answer": "ok", "chunks": [{"chunk_id": "c1"}],
        "shock_vector": {"USDTWD": {"value": 8.0, "unit": "pct", "chunk_ids": ["c1"], "grounding_mode": "direct"}},
        "world_view_id": "macro-2026-10-01",
        "synthesis_diagnostics": {"outcome": "grounded", "coverage_gap": False, "kept_fields": ["USDTWD"]},
    }
    for k, v in overrides.items():
        if v is _DELETE:
            body.pop(k, None)
        else:
            body[k] = v
    return body


_DELETE = object()


def _scen(legs: list[tuple[str, list[str]]], verdicts: dict[str, str] | None = None) -> CalibratedScenario:
    """legs: (factor, cited chunk ids); verdicts: factor -> verify verdict (None = no verify record)."""
    return CalibratedScenario(
        name="spec_scenario",
        legs=[DriverLeg(factor=f, move=-0.05, origin="macro" if not f.startswith("eq:") else "equity",
                        citations=[Citation(chunk_id=c, source="macro") for c in cites]) for f, cites in legs],
        verification=None if verdicts is None else {
            "n_dropped": 0, "verdicts": [{"factor": f, "verdict": v, "reason": "r"} for f, v in verdicts.items()],
            "fact_check": {"n_clamped": 0, "n_flagged": 0, "verdicts": []}},
    )


_GOOD_LEGS = [("fx:TWD", ["c1"]), ("eq:NVDA", ["arag-1"])]
_ALL_SUP = {"fx:TWD": "SUPPORTED", "eq:NVDA": "SUPPORTED"}
_ALL_UNSUP = {"fx:TWD": "UNSUPPORTED", "eq:NVDA": "UNSUPPORTED"}

# (case id, scenario, synthesis, expected reasons): Requirement 3 applied by hand to each row.
_TABLE = [
    ("clean", _scen(_GOOD_LEGS, _ALL_SUP), _synth(), ()),
    ("clean_no_verify_record", _scen(_GOOD_LEGS), _synth(), ()),
    ("all_unsupported", _scen(_GOOD_LEGS, _ALL_UNSUP), _synth(), ("all_legs_unsupported",)),
    ("all_unsupported_mixed_case", _scen(_GOOD_LEGS, {"fx:TWD": "unsupported", "eq:NVDA": "Unsupported"}),
     _synth(), ("all_legs_unsupported",)),
    ("one_leg_supported", _scen(_GOOD_LEGS, {"fx:TWD": "UNSUPPORTED", "eq:NVDA": "SUPPORTED"}), _synth(), ()),
    ("one_leg_without_verdict", _scen(_GOOD_LEGS, {"fx:TWD": "UNSUPPORTED"}), _synth(), ()),
    ("gap_flag", _scen(_GOOD_LEGS, _ALL_SUP),
     _synth(synthesis_diagnostics={"outcome": "coverage_gap", "coverage_gap": True}), ("coverage_gap",)),
    ("gap_empty_vector", _scen(_GOOD_LEGS, _ALL_SUP), _synth(shock_vector={}), ("coverage_gap",)),
    ("gap_missing_vector", _scen(_GOOD_LEGS, _ALL_SUP), _synth(shock_vector=_DELETE), ("coverage_gap",)),
    ("gap_flag_and_empty_once", _scen(_GOOD_LEGS, _ALL_SUP),
     _synth(shock_vector={}, synthesis_diagnostics={"coverage_gap": True}), ("coverage_gap",)),
    ("no_diagnostics_block", _scen(_GOOD_LEGS, _ALL_SUP), _synth(synthesis_diagnostics=_DELETE), ()),
    ("wv_absent", _scen(_GOOD_LEGS, _ALL_SUP), _synth(world_view_id=_DELETE), ("missing_world_view_id",)),
    ("wv_empty", _scen(_GOOD_LEGS, _ALL_SUP), _synth(world_view_id=""), ("missing_world_view_id",)),
    ("wv_none", _scen(_GOOD_LEGS, _ALL_SUP), _synth(world_view_id=None), ("missing_world_view_id",)),
    ("wv_not_string", _scen(_GOOD_LEGS, _ALL_SUP), _synth(world_view_id=20261001), ("missing_world_view_id",)),
    ("uncited", _scen([("fx:TWD", ["c1"]), ("eq:NVDA", [])], _ALL_SUP), _synth(), ("uncited_leg",)),
    ("two_uncited_once", _scen([("fx:TWD", []), ("eq:NVDA", [])], _ALL_SUP), _synth(), ("uncited_leg",)),
    ("unseen", _scen([("fx:TWD", ["macro-001"]), ("eq:NVDA", ["arag-1"])], _ALL_SUP), _synth(),
     ("unseen_citation",)),
    ("unseen_among_seen", _scen([("fx:TWD", ["c1", "c9"]), ("eq:NVDA", ["arag-1"])], _ALL_SUP), _synth(),
     ("unseen_citation",)),
    ("every_reason_in_order", _scen([("fx:TWD", ["macro-001"]), ("eq:NVDA", [])], _ALL_UNSUP),
     _synth(world_view_id=_DELETE, shock_vector={}, synthesis_diagnostics={"coverage_gap": True}), REASONS),
    ("unsupported_and_uncited", _scen([("fx:TWD", ["c1"]), ("eq:NVDA", [])], _ALL_UNSUP), _synth(),
     ("all_legs_unsupported", "uncited_leg")),
    ("no_legs", _scen([], {}), _synth(), ()),
]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("scen", "synth", "expected"), [t[1:] for t in _TABLE], ids=[t[0] for t in _TABLE])
def test_c2_rule_table(scen, synth, expected):
    # oracle: Requirement 3's rule table applied by hand to each hand-built row (reason codes in the
    # spec's order, each at most once; ok iff no reason). A scenario with no legs trips no leg rule.
    verdict = assess_grounding(scen, synthesis=synth, seen_ids=SEEN)
    assert isinstance(verdict, GroundingVerdict)
    assert verdict.reasons == expected
    assert verdict.ok is (expected == ())


@pytest.mark.criterion("C2")
def test_c2_defect_run_is_refused():
    # oracle: spec C2 defect case (a live run) by hand: 16 legs all UNSUPPORTED ->
    # all_legs_unsupported; macro legs cite macro-001..003, none returned by any tool -> unseen_citation.
    # The synthesis itself is well formed, so no other rule holds.
    macro = ["ir:DGS2", "ir:DGS5", "ir:DGS10", "ir:DGS30", "credit", "fx:TWD", "fx:JPY", "eqvol"]
    equity = ["eq:NVDA", "eq:TSM", "eq:AMD", "eq:AVGO", "eq:MSFT", "eq:GOOGL", "eq:META", "eq:INTC"]
    legs = [(f, [f"macro-00{i % 3 + 1}"]) for i, f in enumerate(macro)] + [(f, ["arag-1"]) for f in equity]
    scen = _scen(legs, {f: "UNSUPPORTED" for f, _ in legs})
    assert len(scen.legs) == 16
    verdict = assess_grounding(scen, synthesis=_synth(), seen_ids=SEEN)
    assert verdict.ok is False
    assert verdict.reasons == ("all_legs_unsupported", "unseen_citation")


@st.composite
def _case(draw: Any) -> tuple[CalibratedScenario, dict[str, Any], frozenset[str], tuple[str, ...]]:
    """A scenario + synthesis + seen ids built so that exactly a drawn subset of the rules holds."""
    flags = {r: draw(st.booleans()) for r in REASONS}
    n = draw(st.integers(2, 6))
    factors = [f"eq:N{i}" if i % 2 else f"ir:F{i}" for i in range(n)]
    seen = {f"s-{i}" for i in range(n)} | set(draw(st.lists(_ID, max_size=3)))
    cites: list[list[str]] = [[f"s-{i}"] for i in range(n)]
    if flags["unseen_citation"]:
        cites[0] = [*draw(st.lists(st.sampled_from(sorted(seen)), max_size=1)), "ghost-" + draw(_ID)]
    if flags["uncited_leg"]:
        for i in draw(st.sets(st.integers(1, n - 1), min_size=1)):
            cites[i] = []
    if flags["all_legs_unsupported"]:
        verdicts = {f: draw(st.sampled_from(["UNSUPPORTED", "unsupported", "Unsupported"])) for f in factors}
    else:
        verdicts = {f: draw(st.sampled_from(["SUPPORTED", "UNSUPPORTED"])) for f in factors}
        verdicts[factors[draw(st.integers(0, n - 1))]] = "SUPPORTED"
    synth = _synth()
    if flags["coverage_gap"]:
        variant = draw(st.sampled_from(["flag", "empty", "missing", "both"]))
        if variant in ("flag", "both"):
            synth["synthesis_diagnostics"] = {"outcome": "coverage_gap", "coverage_gap": True}
        if variant in ("empty", "both"):
            synth["shock_vector"] = {}
        if variant == "missing":
            del synth["shock_vector"]
    if flags["missing_world_view_id"]:
        variant = draw(st.sampled_from(["absent", "", None, 7]))
        if variant == "absent":
            del synth["world_view_id"]
        else:
            synth["world_view_id"] = variant
    scen = _scen(list(zip(factors, cites, strict=True)), verdicts)
    return scen, synth, frozenset(seen), tuple(r for r in REASONS if flags[r])


@pytest.mark.criterion("C2")
@given(case=_case())
def test_c2_property_reasons_match_the_rules_that_hold(case):
    # oracle: by construction each input makes exactly the drawn subset of Requirement 3's rules hold;
    # the expected reasons are that subset in the spec's order, and ok iff it is empty.
    scen, synth, seen, expected = case
    verdict = assess_grounding(scen, synthesis=synth, seen_ids=seen)
    assert verdict.reasons == expected
    assert verdict.ok is (expected == ())
    # Metamorphic: once every cited id is seen, only unseen_citation can go away.
    all_cited = frozenset(c.chunk_id for leg in scen.legs for c in leg.citations)
    widened = assess_grounding(scen, synthesis=synth, seen_ids=seen | all_cited)
    assert widened.reasons == tuple(r for r in expected if r != "unseen_citation")
