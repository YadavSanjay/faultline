"""issuer-scenario-support C1: ``authoring.validate`` accepts per-issuer credit keys.

Oracle: Requirement 1 by hand: a key for which ``factor_keys.is_issuer_credit`` holds (``credit:``
followed by a non-empty name) is accepted as a driver, an overlay and a replay idiosyncratic label;
every key rejected before that is not an issuer key (``credit:``, ``creditx:ORCL``, ``bogus``) is still
rejected with today's message, which names the key.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

UPPER_NAMES = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=10)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("place", ["driver", "overlay", "replay"])
@pytest.mark.parametrize("key", ["credit:ORCL", "credit:META"])
def test_c1_point_issuer_key_accepted(h, key, place):
    # oracle: Requirement 1: credit:ORCL / credit:META are issuer keys (is_issuer_credit), so a minimal
    #   valid scenario carrying one in any of the three places validates with no error.
    assert h.errors_of(h.doc_of(orcl_event=h.scenario_with(key, place))) == []


@pytest.mark.criterion("C1")
def test_c1_point_both_issuers_in_every_place_at_once(h):
    # oracle: Requirement 1: one document using credit:ORCL and credit:META as drivers, overlays and
    #   replay labels (a replay takes no drivers, so the replay is its own scenario) validates.
    doc = h.doc_of(
        issuer_drivers={"narrative": "issuer widening", "required_services": [],
                        "drivers": {"credit:ORCL": {"move_bp": 150}, "credit:META": {"move_bp": 60}},
                        "overlays": {"credit:META": {"move_bp": 20}, "credit:ORCL": {"move_bp": 30}}},
        issuer_replay={"narrative": "oracle cds repricing", "required_services": [],
                       "replay": {"start": "2025-09-01", "end": "2025-12-31",
                                  "idiosyncratic": {"credit:ORCL": "issuer event", "credit:META": "peer"}}},
    )
    assert h.errors_of(doc) == []


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("place", ["driver", "overlay", "replay"])
@pytest.mark.parametrize("bad", ["credit:", "creditx:ORCL", "bogus"])
def test_c1_point_non_issuer_keys_still_rejected(h, bad, place):
    # oracle: Requirement 1: credit: (empty name), creditx:ORCL (other prefix) and bogus are not issuer
    #   keys and were rejected before, so they are still rejected with today's message naming the key;
    #   the issuer scenario in the same document adds no error (so the only error is the bad key's).
    doc = h.doc_of(good_issuer=h.scenario_with("credit:ORCL", place), bad_key=h.scenario_with(bad, place))
    errors = h.errors_of(doc)
    assert errors == [h.rejection_message("bad_key", bad, place)]
    assert repr(bad) in errors[0]


@pytest.mark.criterion("C1")
@given(name=UPPER_NAMES)
def test_c1_property_any_uppercase_issuer_driver_validates(h, name):
    # oracle: Requirement 1 / C1 property: credit:<uppercase name> is an issuer key, so a document with
    #   it as a driver validates.
    assert h.errors_of(h.doc_of(issuer_event=h.scenario_with("credit:" + name, "driver"))) == []


@pytest.mark.criterion("C1")
@given(name=UPPER_NAMES, place=st.sampled_from(["driver", "overlay", "replay"]))
def test_c1_property_issuer_accepted_and_other_prefix_rejected_everywhere(h, name, place):
    # oracle: Requirement 1: credit:<name> is accepted in every place; creditx:<name> is not an issuer key
    #   and was rejected before, so it is still rejected there with today's message.
    doc = h.doc_of(good_issuer=h.scenario_with("credit:" + name, place),
                   bad_key=h.scenario_with("creditx:" + name, place))
    assert h.errors_of(doc) == [h.rejection_message("bad_key", "creditx:" + name, place)]
