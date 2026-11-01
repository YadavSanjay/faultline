"""Config env-var expansion (harness-audit F6): ${VAR} / ${VAR:-default}, partial + recursive."""

from __future__ import annotations

from src.common.config.env import expand_env_vars, expand_str


def test_var_present_is_substituted():
    assert expand_str("${A}", {"A": "x"}) == "x"


def test_default_used_when_unset():
    assert expand_str("${A:-fallback}", {}) == "fallback"


def test_present_wins_over_default():
    assert expand_str("${A:-fallback}", {"A": "real"}) == "real"


def test_unset_without_default_left_verbatim():
    # A typo must stay visible, not silently blank the value.
    assert expand_str("${MISSING}", {}) == "${MISSING}"


def test_partial_substitution_keeps_suffix():
    assert expand_str("${HOME:-/opt/arag}/src", {}) == "/opt/arag/src"
    assert expand_str("${HOME:-/opt/arag}/src", {"HOME": "/repo"}) == "/repo/src"


def test_recursive_over_dict_and_list():
    obj = {
        "cwd": "${ARAG_HOME:-/default}",
        "env": {"PYTHONPATH": "${ARAG_HOME:-/default}/src"},
        "args": ["-m", "${MOD:-server}"],
        "n": 5,
    }
    out = expand_env_vars(obj, {"ARAG_HOME": "/custom"})
    assert out["cwd"] == "/custom"
    assert out["env"]["PYTHONPATH"] == "/custom/src"
    assert out["args"] == ["-m", "server"]
    assert out["n"] == 5  # non-strings untouched
