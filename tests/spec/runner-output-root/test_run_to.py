"""runner-output-root C1-C3: ``runner.run_to`` reads from an input root and writes under an output root.

Oracle: docs/specs/runner-output-root.md, Requirement 1 and the output layout it names (by hand):
inputs come from ``<input_root>/data`` and every file the run writes lands under
``<output_root>/data`` at the path ``run`` uses today relative to the project's ``data/``:
``pricer/PV/<asset_class>/<trade_id>/<date>.json``, ``pricer/PV/summary_<date>.csv``,
``pricer/PV/quality_<date>.json``, ``pricer/store/runs/valuation_date=<date>/*.parquet``,
``pricer/store/results/valuation_type=PV/valuation_date=<date>/**.parquet`` and
``pricer/store/spans/<run_id>.json``. Nothing under ``input_root`` changes.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from src.common import storage
from src.pricer.obs.run_manifest import RunManifestStore
from src.pricer.output.result_store import ResultStore
from src.pricer.runner import run_to

# Selections the property tests draw from: each fixture trade alone, and the whole equity class (the
# fixture trades plus equity trades whose data the fixture lacks; with workers=2 this is the only
# selection that takes the parallel path, which needs more than one trade).
SELECTIONS = st.sampled_from([
    {"trade_id": "TRADE_1"},
    {"trade_id": "TRADE_2"},
    {"asset_class": "equity"},
])


def _run_ids(output_root: Path) -> set[str]:
    """Distinct run ids recorded in the output root's results store."""
    df = storage.query("SELECT DISTINCT run_id FROM @SRC", ResultStore(output_root).dataset_uri)
    return set(df["run_id"]) if len(df) else set()


def _latest_run_id(output_root: Path, iso: str) -> str:
    df = ResultStore(output_root).latest_run(iso)
    ids = set(df["run_id"])
    assert len(ids) == 1, f"expected one latest run id, got {ids}"
    return ids.pop()


# --------------------------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_c1_input_root_untouched_single_trade(h):
    # oracle: Requirement 1 (invariant): nothing under input_root is created, modified or deleted, so
    #   the tree (paths + bytes) after the run equals the tree before it. The run must also have
    #   happened: the fixture prices TRADE_1 ok (Requirement 1: same return value as run).
    inp, out = h.make_input(), h.make_output()
    before = h.tree(inp)
    rows = run_to(out, input_root=inp, trade_id="TRADE_1", valuation_date=h.vdate)
    assert [(r["trade_id"], r["status"]) for r in rows] == [("TRADE_1", "ok")]
    assert h.tree(inp) == before


@pytest.mark.criterion("C1")
@settings(max_examples=6)
@given(selection=SELECTIONS, workers=st.sampled_from([1, 2]))
def test_c1_input_root_untouched_any_selection_and_workers(h, selection, workers):
    # oracle: Requirement 1 (invariant) for every selection and worker count: the input tree is
    #   byte-identical before and after, and every selected fixture trade is in the returned rows.
    inp, out = h.make_input(), h.make_output()
    before = h.tree(inp)
    rows = run_to(out, input_root=inp, valuation_date=h.vdate, workers=workers, **selection)
    got = {r["trade_id"] for r in rows}
    expected = {selection["trade_id"]} if "trade_id" in selection else set(h.trades)
    assert expected <= got
    assert h.tree(inp) == before


# --------------------------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_c2_output_layout_under_output_root(h):
    # oracle: Requirement 1 output layout, written out by hand for TRADE_1 (asset class "equity") on
    #   2026-10-05, valuation type PV (the portfolio default), under <output_root>/data/pricer/.
    inp, out = h.make_input(), h.make_output()
    iso = h.vdate.isoformat()
    rows = run_to(out, input_root=inp, trade_id="TRADE_1", valuation_date=h.vdate)

    assert len(rows) == 1
    assert rows[0]["trade_id"] == "TRADE_1"
    assert rows[0]["status"] == "ok"

    pricer = h.pricer_dir(out)
    assert (pricer / "PV" / "equity" / "TRADE_1" / f"{iso}.json").is_file()
    summary = pricer / "PV" / f"summary_{iso}.csv"
    assert summary.is_file()
    with summary.open(newline="") as f:
        summary_rows = list(csv.DictReader(f))
    assert [r["trade_id"] for r in summary_rows] == ["TRADE_1"]
    assert (pricer / "PV" / f"quality_{iso}.json").is_file()

    runs_part = pricer / "store" / "runs" / f"valuation_date={iso}"
    assert len(list(runs_part.glob("*.parquet"))) >= 1
    results_part = pricer / "store" / "results" / "valuation_type=PV" / f"valuation_date={iso}"
    assert len(list(results_part.rglob("*.parquet"))) >= 1

    run_id = _latest_run_id(out, iso)
    assert (pricer / "store" / "spans" / f"{run_id}.json").is_file()


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_c2_two_output_roots_same_layout_and_pv(h):
    # oracle: Requirement 1 (metamorphic): the output layout does not depend on which empty output root
    #   is used, so two runs over the same input produce the same relative paths once each run's own
    #   run id is replaced by a placeholder, and the same PV exactly (same inputs, same code).
    inp = h.make_input()
    iso = h.vdate.isoformat()
    out_a, out_b = h.make_output(), h.make_output()
    rows_a = run_to(out_a, input_root=inp, trade_id="TRADE_1", valuation_date=h.vdate)
    rows_b = run_to(out_b, input_root=inp, trade_id="TRADE_1", valuation_date=h.vdate)

    run_a, run_b = _latest_run_id(out_a, iso), _latest_run_id(out_b, iso)
    assert run_a != run_b
    paths_a = {p.replace(run_a, "<run_id>") for p in h.files_under(out_a)}
    paths_b = {p.replace(run_b, "<run_id>") for p in h.files_under(out_b)}
    assert paths_a
    assert paths_a == paths_b

    assert rows_a[0]["status"] == rows_b[0]["status"] == "ok"
    # tolerance: zero; a deterministic re-run over identical inputs must give the identical float
    assert rows_a[0]["pv"] == pytest.approx(rows_b[0]["pv"], rel=0, abs=0)


# --------------------------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_c3_second_run_appends_to_same_output_root_only(h):
    # oracle: Requirement 1 (writes are routed per output root): two runs into one output root leave
    #   two distinct run ids in its results store and two run manifests; an independent, empty output
    #   root that no run wrote to has no results and no runs.
    inp, out, other = h.make_input(), h.make_output(), h.make_output()
    iso = h.vdate.isoformat()
    run_to(out, input_root=inp, trade_id="TRADE_1", valuation_date=h.vdate)
    first = _latest_run_id(out, iso)
    run_to(out, input_root=inp, trade_id="TRADE_1", valuation_date=h.vdate)
    second = _latest_run_id(out, iso)

    assert first != second
    assert _run_ids(out) == {first, second}
    assert len(RunManifestStore(out).recent(10)) == 2

    assert len(ResultStore(other).latest_run(iso)) == 0
    assert len(RunManifestStore(other).recent(10)) == 0


@pytest.mark.criterion("C3")
@settings(max_examples=3)
@given(n_runs=st.sampled_from([1, 2, 3]))
def test_c3_n_runs_leave_n_run_ids(h, n_runs):
    # oracle: Requirement 1 (invariant): each run into an output root adds exactly one run id to that
    #   root's store, so n runs leave n distinct run ids and n run manifests; a separate empty output
    #   root stays empty.
    inp, out, other = h.make_input(), h.make_output(), h.make_output()
    for _ in range(n_runs):
        run_to(out, input_root=inp, trade_id="TRADE_2", valuation_date=h.vdate)
    assert len(_run_ids(out)) == n_runs
    assert len(RunManifestStore(out).recent(10)) == n_runs
    assert _run_ids(other) == set()
