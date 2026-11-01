"""llm-grounding-gate C3-C5: the flow synthesizes in code, owns world_view_id, and refuses ungrounded
scenarios before pricing.

Both flow modes run fully offline over the conftest fakes (LLM backend, MCP tool source, pricer).
Expected values come from the spec's Requirements and from what the fakes were scripted to return:
the synthesis fake's `world_view_id`, the fake pricer's record of what it priced, and the shared event
log of LLM and MCP calls.
"""

from __future__ import annotations

import json

import pytest

PNL_REL = 1e-12  # the fake pricer's P&L is the same float arithmetic on the same legs in both runs

GATE_FAILURES = ["all_unsupported", "coverage_gap", "empty_vector", "missing_wv", "uncited", "unseen",
                 "synth_raise", "synth_not_json", "synth_json_list"]


def _bad(gg, key: str, failure: str):
    """A scenario that must be refused for `failure` (a gate reason, or a failed synthesize call)."""
    if failure == "all_unsupported":
        return gg.Scen(key, all_unsupported=True)
    if failure in ("coverage_gap", "empty_vector", "missing_wv"):
        return gg.Scen(key, synth=failure)
    if failure == "uncited":
        return gg.Scen(key, uncited=True)
    if failure == "unseen":
        return gg.Scen(key, unseen=True)
    return gg.Scen(key, synth=failure.removeprefix("synth_"))


def _assert_c3(gg, run, expected_names):
    """C3 checks over one run's event log."""
    synth = run.synth_calls()
    first_cal: dict[str, int] = {}
    for idx, messages in run.calibrate_calls():
        text = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") != "system")
        for name in expected_names:
            if name in text:
                first_cal.setdefault(name, idx)
        for m in messages:
            assert "world_view_id" not in json.dumps(m, default=str), \
                f"a calibrate-stage LLM message mentions world_view_id: {str(m.get('content'))[:200]!r}"
    assert sorted(first_cal) == sorted(expected_names), f"calibrated scenarios {sorted(first_cal)}"
    assert len(synth) == len(expected_names), \
        f"{len(synth)} code-made {gg.SYNTH_TOOL} call(s) for {len(expected_names)} calibrated scenario(s)"
    order = sorted(first_cal.values())
    for (s_idx, args), c_idx in zip(synth, order, strict=True):
        assert s_idx < c_idx, "synthesize must run before that scenario's first calibrate LLM call"
        question = args.get("question")
        assert isinstance(question, str) and question.strip(), f"synthesize question {question!r}"
        assert "world_view_id" not in question


# ----------------------------------------------------------------------------------------- C3


@pytest.mark.criterion("C3")
def test_c3_mode2_code_synthesizes_once_before_each_calibration(tmp_path, gg):
    # oracle: Requirement 1 and C3, read off the shared LLM/MCP event log: the fake model never asks for
    # synthesize, so every synthesize call is the code's; one per calibrated theme, each before that
    # theme's first calibrate call ("concrete DRIVER shocks"); no world_view_id in question or messages.
    scens = [gg.Scen("alpha"), gg.Scen("beta")]
    run = gg.run_mode2(tmp_path, scens)
    _assert_c3(gg, run, [s.name for s in scens])


@pytest.mark.criterion("C3")
def test_c3_mode1_code_synthesizes_once_before_each_calibration(tmp_path, gg):
    # oracle: as above for Mode 1: one code-made synthesize call per calibrated candidate, in order.
    scens = [gg.Scen("alpha"), gg.Scen("beta"), gg.Scen("gamma")]
    run = gg.run_mode1(tmp_path, scens)
    _assert_c3(gg, run, [s.name for s in scens])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("mode", ["mode1", "mode2"])
def test_c3_metamorphic_each_added_scenario_adds_one_synthesize_call(tmp_path, gg, mode):
    # oracle: Requirement 1 (one synthesize call per scenario, by code): going from 1 to 3 scenarios adds
    # exactly 2 synthesize calls, and both runs satisfy the C3 ordering and world_view_id checks.
    runner = gg.run_mode1 if mode == "mode1" else gg.run_mode2
    small = [gg.Scen("alpha")]
    large = [gg.Scen("alpha"), gg.Scen("beta"), gg.Scen("gamma")]
    run_small = runner(tmp_path / "small", small)
    run_large = runner(tmp_path / "large", large)
    assert len(run_large.synth_calls()) - len(run_small.synth_calls()) == 2
    _assert_c3(gg, run_small, [s.name for s in small])
    _assert_c3(gg, run_large, [s.name for s in large])


# ----------------------------------------------------------------------------------------- C4

W = "macro-2026-10-01"


@pytest.mark.criterion("C4")
def test_c4_mode2_world_view_id_comes_from_synthesis(tmp_path, gg):
    # oracle: Requirement 2: the synthesis fake returns world_view_id W; the model writes the defect run's
    # placeholder. The kept scenario and the artifact must carry W; the placeholder appears nowhere.
    run = gg.run_mode2(tmp_path, [gg.Scen("alpha", wv=W, model_wv=gg.PLACEHOLDER_WV)])
    assert run.kept_names == ["scen_alpha"]
    assert run.artifact.calibrated[0].world_view_id == W
    assert run.artifact.world_view_id == W
    assert gg.PLACEHOLDER_WV not in run.artifact.model_dump_json()


@pytest.mark.criterion("C4")
def test_c4_mode1_world_view_id_comes_from_synthesis(tmp_path, gg):
    # oracle: Requirement 2, Mode 1: both candidates' synthesis returns W while the model writes two
    # different wrong values; every kept scenario and the artifact carry W.
    scens = [gg.Scen("alpha", wv=W, model_wv=gg.PLACEHOLDER_WV), gg.Scen("beta", wv=W, model_wv="macro-2026-06-24")]
    run = gg.run_mode1(tmp_path, scens)
    assert sorted(run.kept_names) == ["scen_alpha", "scen_beta"]
    assert [c.world_view_id for c in run.artifact.calibrated] == [W, W]
    assert run.artifact.world_view_id == W


@pytest.mark.criterion("C4")
def test_c4_artifact_world_view_id_only_from_kept_scenarios(tmp_path, gg):
    # oracle: Requirement 2 ("taken only from kept scenarios"): alpha (synthesis id W_A) is refused for an
    # unseen citation, beta (synthesis id W_B) is kept, so the artifact carries W_B and never W_A.
    w_a, w_b = "macro-2026-09-15", "macro-2026-10-01"
    scens = [gg.Scen("alpha", wv=w_a, unseen=True), gg.Scen("beta", wv=w_b)]
    run = gg.run_mode2(tmp_path, scens)
    assert run.kept_names == ["scen_beta"]
    assert run.artifact.world_view_id == w_b
    assert all(c.world_view_id != w_a for c in run.artifact.calibrated)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@pytest.mark.parametrize("mode", ["mode1", "mode2"])
def test_c4_metamorphic_model_value_is_irrelevant_synthesis_value_decides(tmp_path, gg, mode):
    # oracle: Requirement 2: whatever the model writes (omitted, empty, placeholder, another id, or even W)
    # the output is the synthesis value W; changing only the synthesis value to W2 changes the output to W2.
    runner = gg.run_mode1 if mode == "mode1" else gg.run_mode2
    w2 = "macro-2026-09-30"
    for i, model_wv in enumerate([None, "", gg.PLACEHOLDER_WV, "macro-2026-06-24", W]):
        run = runner(tmp_path / f"m{i}", [gg.Scen("alpha", wv=W, model_wv=model_wv)])
        assert [c.world_view_id for c in run.artifact.calibrated] == [W], f"model wrote {model_wv!r}"
        assert run.artifact.world_view_id == W
    run2 = runner(tmp_path / "w2", [gg.Scen("alpha", wv=w2, model_wv=W)])
    assert [c.world_view_id for c in run2.artifact.calibrated] == [w2]
    assert run2.artifact.world_view_id == w2


# ----------------------------------------------------------------------------------------- C5


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("failure", GATE_FAILURES)
def test_c5_mode2_refused_theme_is_not_priced_or_kept(tmp_path, gg, failure):
    # oracle: Requirements 1, 3 and 4: theme alpha fails one gate rule (or its synthesize call fails), so
    # the fake pricer never sees it and it is not in artifact.calibrated; theme beta is grounded and is
    # priced at the fake pricer's 1e8 * sum(moves) and kept.
    beta = gg.Scen("beta")
    run = gg.run_mode2(tmp_path, [_bad(gg, "alpha", failure), beta])
    assert run.priced_names == ["scen_beta"], f"pricer was asked for {run.priced_names}"
    assert run.kept_names == ["scen_beta"]
    assert run.pricer.priced[0][1] == pytest.approx(beta.expected_pnl, rel=PNL_REL)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("failure", GATE_FAILURES)
def test_c5_mode1_refused_candidate_is_not_priced_kept_or_ranked(tmp_path, gg, failure):
    # oracle: Requirements 1, 3 and 4 in Mode 1: candidate beta is refused, so it is not priced, not in
    # artifact.calibrated and not in artifact.ranking; alpha and gamma pass and are priced and kept.
    run = gg.run_mode1(tmp_path, [gg.Scen("alpha"), _bad(gg, "beta", failure), gg.Scen("gamma")])
    assert sorted(run.priced_names) == ["scen_alpha", "scen_gamma"], f"pricer was asked for {run.priced_names}"
    assert sorted(run.kept_names) == ["scen_alpha", "scen_gamma"]
    assert sorted(r.name for r in run.artifact.ranking) == ["scen_alpha", "scen_gamma"]


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("mode", ["mode1", "mode2"])
def test_c5_all_refused_run_returns_an_empty_artifact(tmp_path, gg, mode):
    # oracle: Requirement 4: every scenario refused -> the flow returns normally, nothing priced, an empty
    # artifact.calibrated (so the runner writes no StressReport) and, in Mode 1, an empty ranking.
    runner = gg.run_mode1 if mode == "mode1" else gg.run_mode2
    scens = [gg.Scen("alpha", unseen=True), gg.Scen("beta", all_unsupported=True), gg.Scen("gamma", synth="raise")]
    run = runner(tmp_path, scens)
    assert run.priced_names == []
    assert run.artifact.calibrated == []
    assert run.artifact.ranking == []
    assert run.artifact.world_view_id == ""


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@pytest.mark.parametrize("mode", ["mode1", "mode2"])
def test_c5_metamorphic_unseen_citation_removes_exactly_that_scenario(tmp_path, gg, mode):
    # oracle: spec C5 metamorphic relation: making beta's macro citation unseen removes exactly beta from
    # the output and leaves alpha's and gamma's priced P&L (fake pricer record) unchanged.
    runner = gg.run_mode1 if mode == "mode1" else gg.run_mode2
    base = runner(tmp_path / "base", [gg.Scen("alpha"), gg.Scen("beta"), gg.Scen("gamma")])
    mutated = runner(tmp_path / "mut", [gg.Scen("alpha"), gg.Scen("beta", unseen=True), gg.Scen("gamma")])
    assert sorted(base.kept_names) == ["scen_alpha", "scen_beta", "scen_gamma"], "precondition: all grounded"
    assert sorted(mutated.kept_names) == ["scen_alpha", "scen_gamma"]
    assert sorted(mutated.priced_names) == ["scen_alpha", "scen_gamma"]
    base_pnl = dict(base.pricer.priced)
    for name, pnl in mutated.pricer.priced:
        assert pnl == pytest.approx(base_pnl[name], rel=PNL_REL), name
    if mode == "mode1":
        base_rank = {r.name: r.total_pnl for r in base.artifact.ranking if r.name != "scen_beta"}
        mut_rank = {r.name: r.total_pnl for r in mutated.artifact.ranking}
        assert sorted(mut_rank) == sorted(base_rank)
        for name, pnl in mut_rank.items():
            assert pnl == pytest.approx(base_rank[name], rel=PNL_REL), name
