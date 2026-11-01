"""measurement-views C3: ``report_views.replay_view`` builds the event-replay view.

Oracle: Requirement 3 by hand: limitations copied ([] when absent); episodes in report order with name,
as_of, horizon_days, available copied; authored / enriched = {nearest, cosine = scores[nearest].cosine}
(None when nearest is None or has no score); gain = enriched.cosine - authored.cosine, None when either
is None; dropped = the dropped scenario names in report order; regime_asof / dropped_issuer_factors from
pit (None / [] when absent). Inputs are hand-written dicts (conftest).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.report_views import replay_view


@pytest.mark.criterion("C3")
def test_c3_point_hand_built_report(h):
    # oracle: Requirement 3 by hand (conftest.replay_view): covid gain 0.75 - 0.5 = +0.25 with pit copied
    #   and dropped [taiwan, ai_bust]; gfc enriched nearest None -> cosine None, gain None, no pit ->
    #   regime_asof None and dropped_issuer_factors []; no limitations key -> [].
    h.assert_close(replay_view(h.replay_report()), h.replay_view())


@pytest.mark.criterion("C3")
def test_c3_point_negative_gain_unscored_nearest_and_limitations(h):
    # oracle: Requirement 3 by hand: authored nearest equity_crash cosine 0.75, enriched nearest rates_up
    #   cosine 0.25 -> gain 0.25 - 0.75 = -0.5; a second episode whose authored nearest has no entry in
    #   scores -> cosine None -> gain None; limitations copied; pit with regime_asof None kept as None.
    rep = {
        "limitations": ["replay cap on partial history", "issuer factors dropped before DTCC start"],
        "episodes": [
            {"name": "taper_2013", "as_of": "2013-05-22", "horizon_days": 30, "available": ["rates_up"],
             "dropped": [{"scenario": "taiwan", "reason": "r", "detail": ""}],
             "versions": {"authored": h.version("equity_crash", {"equity_crash": 0.75, "rates_up": 0.5}),
                          "enriched": h.version("rates_up", {"equity_crash": 0.125, "rates_up": 0.25})},
             "pit": {"regime_asof": None, "dropped_issuer_factors": []}},
            {"name": "svb_2023", "as_of": "2023-03-10", "horizon_days": 5, "available": [], "dropped": [],
             "versions": {"authored": h.version("bank_run", {"rates_up": 0.5}),
                          "enriched": h.version("rates_up", {"rates_up": 0.625})},
             "pit": {"regime_asof": "2023-03-03", "dropped_issuer_factors": ["CR_SIVB"]}},
        ],
    }
    expected = {
        "limitations": ["replay cap on partial history", "issuer factors dropped before DTCC start"],
        "episodes": [
            {"name": "taper_2013", "as_of": "2013-05-22", "horizon_days": 30,
             "authored": {"nearest": "equity_crash", "cosine": 0.75},
             "enriched": {"nearest": "rates_up", "cosine": 0.25}, "gain": -0.5,
             "available": ["rates_up"], "dropped": ["taiwan"], "regime_asof": None, "dropped_issuer_factors": []},
            {"name": "svb_2023", "as_of": "2023-03-10", "horizon_days": 5,
             "authored": {"nearest": "bank_run", "cosine": None},
             "enriched": {"nearest": "rates_up", "cosine": 0.625}, "gain": None,
             "available": [], "dropped": [], "regime_asof": "2023-03-03", "dropped_issuer_factors": ["CR_SIVB"]},
        ],
    }
    h.assert_close(replay_view(rep), expected)


@pytest.mark.criterion("C3")
def test_c3_point_report_not_mutated(h):
    # oracle: Requirement 3 by hand: the report equals a deep copy taken before the call; the view is the
    #   hand view.
    rep = h.replay_report()
    before = copy.deepcopy(rep)
    out = replay_view(rep)
    assert rep == before
    h.assert_close(out, h.replay_view())


COSINE = st.one_of(st.none(), st.integers(-16, 16).map(lambda k: k / 16))
SCEN = ["equity_crash", "rates_up", "taiwan", "credit_crunch"]


@st.composite
def _version(draw):
    scores = {n: draw(COSINE) for n in draw(st.lists(st.sampled_from(SCEN), unique=True))}
    nearest = draw(st.one_of(st.none(), st.sampled_from(SCEN)))
    return {"nearest": nearest, "scores": {n: {"cosine": c} for n, c in scores.items()}}


@st.composite
def _replay_report(draw):
    n = draw(st.integers(1, 5))
    episodes = []
    for i in range(n):
        ep = {"name": f"ep{i}_{draw(st.integers(0, 9))}", "as_of": "2020-01-02", "horizon_days": 10,
              "available": [], "dropped": [{"scenario": s, "reason": "r"} for s in
                                           draw(st.lists(st.sampled_from(SCEN), unique=True))],
              "versions": {"authored": draw(_version()), "enriched": draw(_version())}}
        if draw(st.booleans()):
            ep["pit"] = {"regime_asof": "2019-12-31", "dropped_issuer_factors": ["CR_X"]}
        episodes.append(ep)
    rep = {"episodes": episodes}
    if draw(st.booleans()):
        rep["limitations"] = ["note"]
    return rep


def _cosine(block):
    nearest = block["nearest"]
    if nearest is None or nearest not in block["scores"]:
        return None
    return block["scores"][nearest]["cosine"]


@pytest.mark.criterion("C3")
@given(rep=_replay_report())
def test_c3_property_gain_is_difference_or_none(rep):
    # oracle: Requirement 3 by hand: cosine = scores[nearest].cosine (None when nearest is None or
    #   unscored); gain is None exactly when either cosine is None, else enriched - authored.
    out = replay_view(rep)
    assert len(out["episodes"]) == len(rep["episodes"])
    for e, src in zip(out["episodes"], rep["episodes"], strict=True):
        a, b = _cosine(src["versions"]["authored"]), _cosine(src["versions"]["enriched"])
        assert e["authored"]["cosine"] == a
        assert e["enriched"]["cosine"] == b
        if a is None or b is None:
            assert e["gain"] is None
        else:
            # tolerance: cosines are k/16, so the difference is exact; approx only for symmetry.
            assert e["gain"] == pytest.approx(b - a, rel=1e-12, abs=1e-12)


@pytest.mark.criterion("C3")
@given(rep=_replay_report())
def test_c3_property_episode_order_and_dropped_names(rep):
    # oracle: Requirement 3 by hand: episodes follow the report order; dropped lists the scenario names in
    #   report order; pit absent -> regime_asof None and dropped_issuer_factors [].
    out = replay_view(rep)
    assert [e["name"] for e in out["episodes"]] == [e["name"] for e in rep["episodes"]]
    assert out["limitations"] == rep.get("limitations", [])
    for e, src in zip(out["episodes"], rep["episodes"], strict=True):
        assert e["dropped"] == [d["scenario"] for d in src["dropped"]]
        pit = src.get("pit")
        assert e["regime_asof"] == (pit["regime_asof"] if pit else None)
        assert e["dropped_issuer_factors"] == (pit["dropped_issuer_factors"] if pit else [])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(rep=_replay_report())
def test_c3_property_input_unchanged_and_reversal(rep):
    # oracle: Requirement 3 by hand: the report equals a deep copy taken before the call, and reversing
    #   the report's episodes reverses the view's episodes (each episode is built on its own).
    before = copy.deepcopy(rep)
    out = replay_view(rep)
    assert rep == before
    assert len(out["episodes"]) == len(rep["episodes"])
    rev = copy.deepcopy(rep)
    rev["episodes"].reverse()
    assert replay_view(rev)["episodes"] == list(reversed(out["episodes"]))
