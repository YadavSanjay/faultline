"""dualrag-macro-gate C3-C4: a cited ``market_episode`` chunk skips only the freshness comparison; the
field's ``grounding_mode`` plays no part in the gate.

Expected values are hand-computed from the spec (docs/specs/dualrag-macro-gate.md, Requirement 4 and the
Notes' canonical conversion: bp x 1e-4, FX pct / 100, VIX pts / 100, MOVE pts / move_base=100). Chunk
ages are counted by hand from the fixed valuation date 2026-10-06 (conftest.py docstring).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.errors import ScenarioGenerationError

FD = 550  # committed macro.json gate.freshness_days
EPISODE = "market_episode"

# Field pool: (shock value, unit, canonical targets, canonical move by hand from the Notes' conversion).
POOL = {
    "USDTWD": (5.0, "pct", ("fx:TWD",), 0.05),          # 5 pct / 100
    "USDJPY": (-3.0, "pct", ("fx:JPY",), -0.03),        # -3 pct / 100
    "VIX": (10.0, "pts", ("eqvol",), 0.10),             # 10 pts / 100
    "MOVE": (40.0, "pts", ("ratesvol",), 0.40),         # 40 pts / move_base 100
    "IG_OAS": (80.0, "bp", ("credit",), 0.008),         # 80 bp x 1e-4
    "UST": (-25.0, "bp", tuple(f"ir:{n}" for n in ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS3",
                                                     "DGS5", "DGS7", "DGS10", "DGS20", "DGS30")), -0.0025),
}
MODES = ["analog", "direct", None, "historical", ""]
DOC_TYPES = [EPISODE, None, "research_paper", "speech", "Market_Episode", "market_episode_v2"]
DEFECT_PAIRS = (("fx:TWD", "k1"), ("fx:TWD", "k2"), ("eqvol", "k3"), ("ir:DGS10", "k4"))


def _iso(days_before: int) -> str:
    return (date(2026, 10, 6) - timedelta(days=days_before)).isoformat()


# ----------------------------------------------------------------------------------------- C3


@pytest.mark.criterion("C3")
def test_c3_defect_case_fails_with_four_findings_unless_the_chunks_are_episode_cards(dm):
    # oracle: spec C3 defect case by hand: ages 2026-10-06 - 2025-02-26 = 587 > 550 for all 4 chunks.
    # Non-episode chunks -> one stale finding per (driver, chunk) pair: (fx:TWD,k1) (fx:TWD,k2) (eqvol,k3)
    # (ir:DGS10,k4) = 4, for every grounding_mode (Requirement 3: mode is provenance only). Episode chunks
    # -> freshness skipped, nothing else wrong -> pass.
    for mode in ("analog", "direct", None):
        for doc_type in (None, "research_paper"):
            res = dm.gate_check(dm.defect_response(mode, doc_type))
            assert res.passed is False
            assert len(res.findings) == 4
            for factor, cid in DEFECT_PAIRS:
                assert sum(1 for f in res.findings if factor in f and cid in f and "stale" in f) == 1
        episode = dm.gate_check(dm.defect_response(mode, EPISODE))
        assert episode.passed is True and episode.findings == []


@pytest.mark.criterion("C3")
def test_c3_episode_defect_case_applies_the_macro_legs(dm):
    # oracle: Notes conversion by hand: USDTWD +5 pct -> fx:TWD 0.05; VIX +10 pts -> eqvol 0.10;
    # UST_10Y -30 bp -> ir:DGS10 -0.0030; the static equity leg eq:TSM -25 pct -> -0.25 is kept.
    expected = {"fx:TWD": 0.05, "eqvol": 0.10, "ir:DGS10": -0.0030, "eq:TSM": -0.25}
    resp = dm.defect_response("direct", EPISODE)
    res = dm.macro_provider(resp=resp).resolve("taiwan_geopolitical")
    assert res.used_macro is True
    assert res.drivers == pytest.approx(expected, rel=1e-12)  # exact unit conversions, float division only
    prov = dm.dual(dm.macro_provider(resp=resp), "taiwan_geopolitical", ["macro", "arag"])
    for tier, scale in dm.TIERS.items():
        defn, dual = prov.definition_for("taiwan_geopolitical", severity=tier, severity_scale=scale)
        assert dual.used_macro is True
        assert defn.drivers == pytest.approx(expected, rel=1e-12)  # drivers are unscaled until materialize


@pytest.mark.criterion("C3")
def test_c3_stale_analog_paper_stops_a_required_scenario(dm):
    # oracle: Requirement 4 + 1: the 2026-10-13 taiwan case (analog fields citing a 587-day-old paper) is no
    # longer exempt -> macro_gate_failed on every tier; the 4 hand-counted pairs appear in detail.
    prov = dm.dual(dm.macro_provider(resp=dm.defect_response("analog", "research_paper")),
                   "taiwan_geopolitical", ["macro", "arag"])
    for tier, scale in dm.TIERS.items():
        with pytest.raises(ScenarioGenerationError) as ei:
            prov.definition_for("taiwan_geopolitical", severity=tier, severity_scale=scale)
        assert ei.value.reason == "macro_gate_failed"
        for factor, cid in DEFECT_PAIRS:
            assert factor in ei.value.detail and cid in ei.value.detail


@pytest.mark.criterion("C3")
@given(chosen=st.lists(st.sampled_from(sorted(POOL)), min_size=1, max_size=6, unique=True),
       data=st.data())
def test_c3_property_only_stale_non_episode_pairs_are_findings(dm, chosen, data):
    # oracle: Requirement 4 as a counting model: with every chunk present, dated and cited, a finding is a
    # (driver, chunk) pair whose chunk is older than 550 days and whose doc_type is not exactly
    # "market_episode"; grounding_mode is irrelevant. Ages hand-set via as_of - timedelta (days); age 550
    # itself is not stale (strictly older fails).
    ages = data.draw(st.lists(st.integers(min_value=0, max_value=1500), min_size=3, max_size=3), label="ages")
    ages[0] = data.draw(st.integers(min_value=FD + 1, max_value=1500), label="stale_age")
    kinds = data.draw(st.lists(st.sampled_from(DOC_TYPES), min_size=3, max_size=3), label="doc_types")
    chunks = [dm.chunk(f"c{i}", _iso(a), kinds[i]) for i, a in enumerate(ages)]
    shock, expected_findings, expected_moves = {}, 0, {}
    for name in chosen:
        value, unit, targets, move = POOL[name]
        mode = data.draw(st.sampled_from(MODES), label=f"mode_{name}")
        cids = data.draw(st.lists(st.sampled_from(["c0", "c1", "c2"]), min_size=1, max_size=3, unique=True),
                         label=f"cids_{name}")
        shock[name] = dm.field(value, unit, cids, mode)
        stale = sum(1 for c in cids if ages[int(c[1])] > FD and kinds[int(c[1])] != EPISODE)
        expected_findings += stale * len(targets)
        expected_moves.update(dict.fromkeys(targets, move))
    resp = dm.response(shock, chunks)
    res = dm.gate_check(resp)
    assert len(res.findings) == expected_findings
    assert res.passed is (expected_findings == 0)
    resolved = dm.macro_provider(resp=resp).resolve("taiwan_geopolitical")
    assert resolved.used_macro is (expected_findings == 0)
    if expected_findings == 0:
        for factor, move in expected_moves.items():
            assert resolved.drivers[factor] == pytest.approx(move, rel=1e-12)  # exact unit conversion


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_c3_relabelling_chunks_as_episodes_clears_exactly_the_stale_findings(dm):
    # oracle: Requirement 4: changing only doc_type to "market_episode" removes the stale findings and
    # nothing else. Hand count: VIX cites stale s1 + undated u1, UST_10Y cites stale s1 + absent ghost ->
    # 2 stale + 2 missing/undated findings before; after relabelling every chunk, the 2 non-stale remain.
    def resp(doc_type):
        return dm.response({"VIX": dm.field(10.0, "pts", ["s1", "u1"], "analog"),
                            "UST_10Y": dm.field(-30.0, "bp", ["s1", "ghost"], "direct")},
                           [dm.chunk("s1", dm.STALE_DATE, doc_type), dm.chunk("u1", "", doc_type)])
    before = dm.gate_check(resp("research_paper")).findings
    after = dm.gate_check(resp(EPISODE)).findings
    assert len(before) == 4 and sum(1 for f in before if "stale" in f) == 2
    assert len(after) == 2 and not any("stale" in f for f in after)
    for cid in ("u1", "ghost"):
        assert sum(1 for f in after if cid in f) == 1


# ----------------------------------------------------------------------------------------- C4


@pytest.mark.criterion("C4")
def test_c4_uncited_driver_still_fails_next_to_an_episode_citing_driver(dm):
    # oracle: Requirement 4: the citation-count rule still applies. eqvol (VIX) cites nothing -> 1 finding;
    # fx:TWD (USDTWD) cites the 587-day-old episode card k1 -> no finding.
    resp = dm.response({"VIX": dm.field(10.0, "pts", [], "direct"),
                        "USDTWD": dm.field(5.0, "pct", ["k1"], "direct")},
                       [dm.chunk("k1", dm.STALE_DATE, EPISODE)])
    res = dm.gate_check(resp)
    assert res.passed is False
    assert len(res.findings) == 1 and "eqvol" in res.findings[0]
    with pytest.raises(ScenarioGenerationError) as ei:
        dm.dual(dm.macro_provider(resp=resp), "taiwan_geopolitical", ["macro"]).definition_for("taiwan_geopolitical")
    assert ei.value.reason == "macro_gate_failed"


@pytest.mark.criterion("C4")
def test_c4_episode_citing_driver_with_absent_or_undated_chunk_fails(dm):
    # oracle: Requirement 4: each cited chunk must be present and dated, episode card or not. USDTWD cites the
    # stale episode k1 (no finding) plus one bad chunk (exactly 1 finding naming it): absent "ghost", an
    # undated episode card "ue", an undated paper "u1".
    for bad_id, extra in (("ghost", []), ("ue", [dm.chunk("ue", "", EPISODE)]), ("u1", [dm.chunk("u1", "")])):
        resp = dm.response({"USDTWD": dm.field(5.0, "pct", ["k1", bad_id], "analog")},
                           [dm.chunk("k1", dm.STALE_DATE, EPISODE), *extra])
        res = dm.gate_check(resp)
        assert res.passed is False
        assert len(res.findings) == 1 and bad_id in res.findings[0] and "fx:TWD" in res.findings[0]
        with pytest.raises(ScenarioGenerationError) as ei:
            dm.dual(dm.macro_provider(resp=resp), "taiwan_geopolitical", ["macro"]).definition_for(
                "taiwan_geopolitical")
        assert ei.value.reason == "macro_gate_failed"


@pytest.mark.criterion("C4")
def test_c4_episode_plus_stale_paper_is_one_finding_for_the_paper(dm):
    # oracle: Requirement 4 is per cited chunk: e1 (episode) and p1 (paper) are both 587 days old; VIX cites
    # both -> exactly one stale finding, naming p1, none naming e1.
    resp = dm.response({"VIX": dm.field(10.0, "pts", ["e1", "p1"], "analog")},
                       [dm.chunk("e1", dm.STALE_DATE, EPISODE), dm.chunk("p1", dm.STALE_DATE, "research_paper")])
    res = dm.gate_check(resp)
    assert res.passed is False
    assert len(res.findings) == 1
    assert "eqvol" in res.findings[0] and "p1" in res.findings[0] and "stale" in res.findings[0]
    assert not any("e1" in f for f in res.findings)


@pytest.mark.criterion("C4")
@given(chosen=st.lists(st.sampled_from(sorted(POOL)), min_size=1, max_size=6, unique=True), data=st.data())
def test_c4_property_finding_count_model(dm, chosen, data):
    # oracle: Requirement 4 as a counting model over a hand-built chunk pool: s1 stale paper (587 days),
    # e1 stale episode, f1 fresh paper (35 days), u1 undated paper, ue undated episode, ghost absent.
    # Per driver: 1 if it cites nothing; +1 per cited u1/ue/ghost; +1 if it cites s1. e1 and f1 never count.
    chunks = [dm.chunk("s1", dm.STALE_DATE, "research_paper"), dm.chunk("e1", dm.STALE_DATE, EPISODE),
              dm.chunk("f1", dm.FRESH_DATE), dm.chunk("u1", ""), dm.chunk("ue", "", EPISODE)]
    shock, expected = {}, 0
    for name in chosen:
        value, unit, targets, _ = POOL[name]
        mode = data.draw(st.sampled_from(MODES), label=f"mode_{name}")
        cids = data.draw(st.lists(st.sampled_from(["s1", "e1", "f1", "u1", "ue", "ghost"]), max_size=6, unique=True),
                         label=f"cids_{name}")
        shock[name] = dm.field(value, unit, cids, mode)
        per_driver = (1 if not cids else 0) + sum(1 for c in cids if c in ("u1", "ue", "ghost", "s1"))
        expected += per_driver * len(targets)
    res = dm.gate_check(dm.response(shock, chunks))
    assert len(res.findings) == expected
    assert res.passed is (expected == 0)
