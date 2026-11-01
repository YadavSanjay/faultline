"""cost-per-layer C1: ``layer_cost.call_layer`` maps a traced call to its enrichment layer.

Oracle: Requirement 1 by hand: "macrorag" for service "macrorag"; for service "arag",
"xbrlrag_narrative" when "/narrative/" is in the purpose and "xbrlrag_facts" when "/facts/" is;
"overhead" for everything else. news / crowding / state make no calls, so nothing maps to them.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.layer_cost import call_layer

ALLOWED = {"macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state", "overhead"}
SERVICES = st.one_of(st.sampled_from(["macrorag", "arag", "llm", "edgar", ""]), st.text(max_size=12))
PURPOSES = st.one_of(
    st.sampled_from(["/query", "/api/v1/narrative/search", "/api/v1/facts/lookup", "/health", "narrative",
                     "facts", "/api/v1/entities/search", ""]),
    st.text(max_size=30),
)
NO_MARKER = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-.?=", max_size=15)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    ("service", "purpose", "expected"),
    [
        ("macrorag", "/query", "macrorag"),                                # MacroRAG /query
        ("arag", "/api/v1/narrative/search", "xbrlrag_narrative"),         # XbrlRag narrative path
        ("arag", "/api/v1/facts/lookup", "xbrlrag_facts"),                 # XbrlRag facts path
        ("arag", "/api/v1/entities/search", "overhead"),                   # another XbrlRag path
        ("arag", "/health", "overhead"),                                   # health probe
        ("llm", "narrative", "overhead"),                                  # the narrative LLM call
        ("llm", "/api/v1/facts/lookup", "overhead"),                       # facts path, but not arag
        ("edgar", "/query", "overhead"),                                   # unknown service
    ],
)
def test_c1_point_each_case_of_requirement_1(service, purpose, expected):
    # oracle: Requirement 1 by hand, one row per bullet (see the comment on each case).
    assert call_layer(service, purpose) == expected


@pytest.mark.criterion("C1")
def test_c1_point_macrorag_any_purpose():
    # oracle: Requirement 1 by hand: a MacroRAG call maps to "macrorag" whatever its purpose (even one
    #   containing "/facts/").
    assert call_layer("macrorag", "/api/v1/facts/lookup") == "macrorag"
    assert call_layer("macrorag", "") == "macrorag"


@pytest.mark.criterion("C1")
@given(service=SERVICES, purpose=PURPOSES)
def test_c1_property_result_is_a_known_layer_never_a_local_one(service, purpose):
    # oracle: Requirement 1 by hand: the only possible results are macrorag, the two xbrlrag layers and
    #   overhead -- always within LAYERS + ("overhead",), never news / crowding / state.
    out = call_layer(service, purpose)
    assert out in ALLOWED
    assert out in {"macrorag", "xbrlrag_narrative", "xbrlrag_facts", "overhead"}


@pytest.mark.criterion("C1")
@given(service=SERVICES.filter(lambda s: s not in {"macrorag", "arag"}), purpose=PURPOSES)
def test_c1_property_any_other_service_is_overhead(service, purpose):
    # oracle: Requirement 1 by hand: neither "macrorag" nor "arag" -> "overhead", whatever the purpose.
    assert call_layer(service, purpose) == "overhead"


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(prefix=NO_MARKER, suffix=NO_MARKER)
def test_c1_metamorphic_marker_anywhere_in_arag_purpose(prefix, suffix):
    # oracle: Requirement 1 by hand: "/narrative/" (resp. "/facts/") *in* the purpose decides, so wrapping
    #   the marker in any text without a slash keeps the layer; without a marker it is overhead.
    assert call_layer("arag", prefix + "/narrative/" + suffix) == "xbrlrag_narrative"
    assert call_layer("arag", prefix + "/facts/" + suffix) == "xbrlrag_facts"
    assert call_layer("arag", prefix + suffix) == "overhead"
