"""macro-curve-coherence C1, C4, C5: the synthesize request carries ``fields`` and ``directions``;
``directions`` is validated at construction; the committed macro.json states every direction.

Expected values come from the spec (docs/specs/macro-curve-coherence.md, Requirements 1, 4, 5) applied by
hand to synthetic configs, and from the Requirement 5 table for the committed config.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.common.config.models import ConfigError
from src.pricer.config import get_project_root
from src.scenariogen.config import load_macro_config

VOCAB = ["UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "IG_OAS", "HY_OAS", "MOVE", "VIX", "USDTWD", "USDJPY"]

# Requirement 5 table, transcribed by hand (row order = expect_fields order; None = no direction declared).
TABLE = {
    "taiwan_geopolitical": [("USDTWD", "up"), ("USDJPY", "down"), ("IG_OAS", "up"), ("HY_OAS", "up"),
                            ("MOVE", "up"), ("VIX", "up"), ("UST_2Y", "down"), ("UST_5Y", "down"),
                            ("UST_10Y", "down"), ("UST_30Y", "down")],
    "ai_deflation": [("IG_OAS", "up"), ("HY_OAS", "up"), ("MOVE", "up"), ("VIX", "up"), ("UST_2Y", None),
                     ("UST_5Y", None), ("UST_10Y", None), ("UST_30Y", None)],
    "rate_capital": [("UST_2Y", "up"), ("UST_5Y", "up"), ("UST_10Y", "up"), ("UST_30Y", "up"), ("MOVE", "up"),
                     ("IG_OAS", "up"), ("TERM_PREMIUM_10Y", "up")],
    "keyman_nvda": [("VIX", "up"), ("IG_OAS", "up"), ("MOVE", "up")],
}


# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_request_carries_fields_and_directions(mc):
    # oracle: Requirement 1 by hand: the synthetic entry's expect_fields (in order) and directions are sent
    # verbatim inside "synthesis", next to horizon_days (rate_capital's committed horizon 10).
    fields = ["UST_10Y", "UST_2Y", "IG_OAS"]
    directions = {"UST_10Y": "up", "UST_2Y": "up", "IG_OAS": "up"}
    cfg = mc.macro_cfg(rate_capital={"expect_fields": fields, "directions": directions})
    t = mc.FakeMacroTransport(resp=mc.response({}))
    mc.provider(cfg, transport=t).resolve("rate_capital")
    opts = mc.synthesis_opts(t)
    assert opts.get("fields") == fields
    assert opts.get("directions") == directions
    assert opts.get("horizon_days") == 10


@pytest.mark.criterion("C1")
def test_c1_empty_or_missing_fields_and_directions_are_omitted(mc):
    # oracle: Requirement 1: an empty or missing expect_fields omits "fields"; an empty or missing
    # directions omits "directions"; horizon_days is still sent.
    for over in ({"expect_fields": [], "directions": {}}, {"expect_fields": None, "directions": None}):
        t = mc.FakeMacroTransport(resp=mc.response({}))
        mc.provider(mc.macro_cfg(ai_deflation=over), transport=t).resolve("ai_deflation")
        opts = mc.synthesis_opts(t)
        assert "fields" not in opts and "directions" not in opts
        assert opts.get("horizon_days") == 10
    # fields without directions: only "fields" is sent
    t = mc.FakeMacroTransport(resp=mc.response({}))
    cfg = mc.macro_cfg(ai_deflation={"expect_fields": ["VIX"], "directions": None})
    mc.provider(cfg, transport=t).resolve("ai_deflation")
    opts = mc.synthesis_opts(t)
    assert opts.get("fields") == ["VIX"] and "directions" not in opts


@pytest.mark.criterion("C1")
@given(fields=st.lists(st.sampled_from(VOCAB), unique=True), data=st.data())
def test_c1_property_request_mirrors_the_config(mc, fields, data):
    # oracle: Requirement 1: for any expect_fields F and any directions D over a subset of F, the request's
    # synthesis.fields == F (or absent when empty) and synthesis.directions == D (or absent when empty).
    keys = data.draw(st.lists(st.sampled_from(fields), unique=True) if fields else st.just([]), label="dir_keys")
    directions = {k: data.draw(st.sampled_from(["up", "down"]), label=f"dir_{k}") for k in keys}
    t = mc.FakeMacroTransport(resp=mc.response({}))
    mc.provider(mc.macro_cfg(keyman_nvda={"expect_fields": fields, "directions": directions}),
                transport=t).resolve("keyman_nvda")
    opts = mc.synthesis_opts(t)
    assert opts.get("fields") == (fields or None)
    assert opts.get("directions") == (directions or None)
    assert opts.get("horizon_days") == 21   # keyman_nvda's committed scenarios.json horizon


# ----------------------------------------------------------------------------------------- C4


@pytest.mark.criterion("C4")
def test_c4_invalid_directions_raise_config_error_at_construction(mc):
    # oracle: Requirement 4: a value other than "up"/"down", or a key outside expect_fields, raises
    # ConfigError when the provider is built; the valid twin constructs.
    for bad in ({"VIX": "sideways"}, {"VIX": "UP"}, {"MOVE": "up"}):
        cfg = mc.macro_cfg(keyman_nvda={"expect_fields": ["VIX", "IG_OAS"], "directions": bad})
        with pytest.raises(ConfigError):
            mc.provider(cfg)
    mc.provider(mc.macro_cfg(keyman_nvda={"expect_fields": ["VIX", "IG_OAS"], "directions": {"VIX": "up"}}))


@pytest.mark.criterion("C4")
@given(value=st.text(min_size=0, max_size=8).filter(lambda s: s not in ("up", "down")),
       scenario=st.sampled_from(sorted(TABLE)))
def test_c4_property_any_other_value_is_rejected(mc, value, scenario):
    # oracle: Requirement 4: exactly "up" and "down" are allowed, in any scenario.
    cfg = mc.macro_cfg(**{scenario: {"expect_fields": ["IG_OAS"], "directions": {"IG_OAS": value}}})
    with pytest.raises(ConfigError):
        mc.provider(cfg)


# ----------------------------------------------------------------------------------------- C5


@pytest.mark.criterion("C5")
def test_c5_committed_macro_config_matches_the_direction_table():
    # oracle: Requirement 5 table, transcribed by hand above (from each synthesis_question and MacroRAG's
    # positive_means sign conventions).
    cfg = load_macro_config(get_project_root())["scenarios"]
    for name, rows in TABLE.items():
        assert cfg[name].get("expect_fields") == [f for f, _ in rows], name
        assert cfg[name].get("directions") == {f: d for f, d in rows if d}, name


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_c5_committed_config_constructs_and_is_sent_as_is(mc):
    # oracle: Requirements 1 + 4 + 5 together: the committed config constructs (no ConfigError) and each
    # scenario's request carries the table's fields and directions unchanged.
    for name, rows in TABLE.items():
        t = mc.FakeMacroTransport(resp=mc.response({}))
        mc.provider(load_macro_config(get_project_root()), transport=t).resolve(name)
        opts = mc.synthesis_opts(t)
        assert opts.get("fields") == [f for f, _ in rows], name
        assert opts.get("directions") == {f: d for f, d in rows if d}, name
