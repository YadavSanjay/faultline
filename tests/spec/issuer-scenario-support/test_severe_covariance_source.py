"""issuer-scenario-support C2: ``catalog.severe_covariance_source`` picks the severe tier's Σ source.

Oracle: Requirement 2 by hand: ``spec["severity"]["severe_sigma"]`` when ``severity`` is a mapping
holding that key, else ``"crisis"``; the value must be the string ``"crisis"`` or ``"production"``,
anything else raises ``ConfigError`` naming the bad value.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.common.config.models import ConfigError
from src.scenariogen.catalog import severe_covariance_source


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("value", ["production", "crisis"])
def test_c2_point_valid_values_returned(h, value):
    # oracle: Requirement 2: a severity mapping holding severe_sigma returns that value as written.
    spec = {"narrative": "issuer event", "drivers": {"credit:ORCL": {"move_bp": 150}},
            "severity": {"lead": "credit:ORCL", "severe_sigma": value}}
    assert severe_covariance_source(spec) == value


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("spec", [
    {"narrative": "no severity", "drivers": {"eq:XLK": {"move_pct": -10}}},
    {"narrative": "severity without the key", "severity": {"lead": "eq:NVDA", "source": "options"}},
    {"narrative": "empty severity", "severity": {}},
    {"narrative": "string severity", "severity": "production"},
    {"narrative": "number severity", "severity": 3},
    {"narrative": "list severity", "severity": ["severe_sigma", "production"]},
    {"narrative": "null severity", "severity": None},
], ids=["absent", "no-key", "empty", "str", "int", "list", "none"])
def test_c2_point_default_is_crisis(h, spec):
    # oracle: Requirement 2: no severity object, a severity mapping without severe_sigma, or a severity
    #   that is not a mapping (a list or a string mentioning "production" included) gives "crisis".
    assert severe_covariance_source(spec) == "crisis"


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("bad", ["Production", "none", "", 1, None], ids=["Production", "none", "empty", "1", "None"])
def test_c2_point_bad_value_raises_config_error(h, bad):
    # oracle: Requirement 2: anything other than the strings "crisis" / "production" (case matters, a
    #   non-string included) raises ConfigError naming the bad value.
    spec = {"narrative": "bad", "severity": {"lead": "credit:ORCL", "severe_sigma": bad}}
    with pytest.raises(ConfigError) as info:
        severe_covariance_source(spec)
    message = str(info.value)
    assert repr(bad) in message or (bad != "" and str(bad) in message)


_KEYS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8)
_JSONISH = st.one_of(st.none(), st.booleans(), st.integers(-5, 5), st.text(max_size=6),
                     st.lists(st.integers(0, 3), max_size=3))
_SIGMA = st.one_of(st.sampled_from(["crisis", "production"]), st.text(max_size=12), st.integers(-3, 3),
                   st.none(), st.booleans())


@st.composite
def _specs(draw):
    """A spec whose severity is absent, a non-mapping, or a mapping with or without severe_sigma."""
    spec = {"narrative": "generated"}
    kind = draw(st.sampled_from(["absent", "non_mapping", "mapping_without", "mapping_with"]))
    if kind == "non_mapping":
        spec["severity"] = draw(_JSONISH)
    elif kind == "mapping_without":
        spec["severity"] = {"lead": "credit:ORCL"}
    elif kind == "mapping_with":
        spec["severity"] = {"lead": "credit:ORCL", "severe_sigma": draw(_SIGMA)}
    return spec


def _outcome(spec):
    """The function's result, or the ConfigError class when it raises ConfigError."""
    try:
        return severe_covariance_source(spec)
    except ConfigError:
        return ConfigError


@pytest.mark.criterion("C2")
@given(spec=_specs())
def test_c2_property_result_is_a_source_or_config_error(h, spec):
    # oracle: Requirement 2 by hand (conftest hand_source): the result is "crisis" or "production" as the
    #   rule says, or a ConfigError exactly when severe_sigma is present and invalid.
    expected = h.hand_source(spec)
    got = _outcome(spec)
    if expected is h.BAD:
        assert got is ConfigError
    else:
        assert got in h.VALID_SOURCES
        assert got == expected


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(spec=_specs(),
       extra_spec=st.dictionaries(_KEYS.filter(lambda k: k != "severity"), _JSONISH, max_size=4),
       extra_sev=st.dictionaries(_KEYS.filter(lambda k: k != "severe_sigma"), _JSONISH, max_size=4))
def test_c2_metamorphic_unrelated_keys_do_not_change_result(h, spec, extra_spec, extra_sev):
    # oracle: Requirement 2: only severity.severe_sigma matters, so adding unrelated keys to the spec and
    #   to a severity mapping leaves the outcome (value or ConfigError) unchanged, and equal to the hand rule.
    widened = {**extra_spec, **spec}
    if isinstance(spec.get("severity"), dict):
        widened["severity"] = {**extra_sev, **spec["severity"]}
    expected = h.hand_source(spec)
    want = ConfigError if expected is h.BAD else expected
    assert _outcome(spec) == want
    assert _outcome(widened) == want
