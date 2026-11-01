"""issuer-credit-factors C5: the per-issuer credit factor key helpers in ``scenariogen.factor_keys``.

Oracle: Requirement 5 by hand: ``issuer_credit_key(i) = "credit:" + i.upper()``; ``is_issuer_credit`` is
true exactly for ``"credit:"`` followed by a non-empty name; ``issuer_credit_name`` returns that name and
raises ``ValueError`` otherwise; the plain ``credit`` key is not an issuer key.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.factor_keys import is_issuer_credit, issuer_credit_key, issuer_credit_name


@pytest.mark.criterion("C5")
def test_c5_point_issuer_credit_key(h):
    # oracle: hand-listed: "credit:" + upper-cased issuer.
    assert issuer_credit_key("ORCL") == "credit:ORCL"
    assert issuer_credit_key("orcl") == "credit:ORCL"
    assert issuer_credit_key("Meta") == "credit:META"


@pytest.mark.criterion("C5")
def test_c5_point_is_issuer_credit(h):
    # oracle: hand-listed: credit:ORCL and credit:orcl (prefix + non-empty name) are issuer keys; credit
    #   (plain factor), credit: (empty name), eq:NVDA and creditx:ORCL (other prefixes) are not.
    assert is_issuer_credit("credit:ORCL") is True
    assert is_issuer_credit("credit:orcl") is True
    for key in ("credit", "credit:", "eq:NVDA", "creditx:ORCL"):
        assert is_issuer_credit(key) is False, key


@pytest.mark.criterion("C5")
def test_c5_point_issuer_credit_name(h):
    # oracle: hand-listed: the name after "credit:", returned as written.
    assert issuer_credit_name("credit:ORCL") == "ORCL"
    assert issuer_credit_name("credit:META") == "META"
    assert issuer_credit_name("credit:orcl") == "orcl"


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("key", ["credit", "credit:", "eq:NVDA", "creditx:ORCL", ""])
def test_c5_point_issuer_credit_name_rejects_non_issuer_keys(h, key):
    # oracle: Requirement 5: issuer_credit_name raises ValueError for any key that is not credit:<name>.
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        issuer_credit_name(key)


@pytest.mark.criterion("C5")
@given(name=st.text(min_size=1, max_size=12).filter(lambda s: ":" not in s))
def test_c5_property_round_trip(h, name):
    # oracle: Requirement 5: key = "credit:" + name.upper() (built here by hand) is an issuer key whose
    #   name is name.upper().
    key = issuer_credit_key(name)
    assert key == "credit:" + name.upper()
    assert is_issuer_credit(key) is True
    assert issuer_credit_name(key) == name.upper()
