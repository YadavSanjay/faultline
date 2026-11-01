"""runner-output-root C4-C5: governance and the service honour the input/output root split.

Oracle: docs/specs/runner-output-root.md, Requirements 2 and 3 (by hand): ``run_governance_to`` writes
exactly one ``pricer/governance/<date>/governance_<run_id>.json`` under ``<output_root>/data`` and
nothing under ``input_root``; ``create_app_at(output_root, input_root=...)`` serves the results stored
under ``output_root`` from ``GET /results/run/<date>``, and an app on an empty output root serves none.
Whether governance passes on the fixture is not asserted (spec C4).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from src.pricer.governance.report import GovernanceReport
from src.pricer.governance_runner import run_governance_to
from src.pricer.runner import run_to
from src.pricer.service import create_app_at


def _served_trade_ids(app_output_root, input_root, iso: str) -> list[str]:
    """Trade ids served by GET /results/run/<iso>; a 404 counts as none (spec C5)."""
    client = TestClient(create_app_at(app_output_root, input_root=input_root))
    resp = client.get(f"/results/run/{iso}")
    if resp.status_code == 404:
        return []
    assert resp.status_code == 200
    return sorted(t["trade_id"] for t in resp.json()["trades"])


# --------------------------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_c4_governance_report_written_under_output_root(h):
    # oracle: Requirement 2 by hand: the report for 2026-10-05 lands at
    #   <output_root>/data/pricer/governance/2026-10-05/governance_<report.run_id>.json and is the only
    #   file under pricer/governance; input_root is byte-identical before and after.
    inp, out = h.make_input(), h.make_output()
    iso = h.vdate.isoformat()
    before = h.tree(inp)
    report = run_governance_to(out, input_root=inp, asset_class=h.asset_class, valuation_date=h.vdate)

    assert isinstance(report, GovernanceReport)
    assert report.valuation_date == iso
    gov = h.pricer_dir(out) / "governance"
    assert h.files_under(gov) == [f"{iso}/governance_{report.run_id}.json"]
    assert h.tree(inp) == before


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_c4_two_governance_runs_two_reports_in_that_root_only(h):
    # oracle: Requirement 2 (metamorphic): a second governance run into the same output root adds a
    #   second report named by its own run id; a separate empty output root gets none; input_root is
    #   byte-identical throughout.
    inp, out, other = h.make_input(), h.make_output(), h.make_output()
    iso = h.vdate.isoformat()
    before = h.tree(inp)
    r1 = run_governance_to(out, input_root=inp, asset_class=h.asset_class, valuation_date=h.vdate)
    r2 = run_governance_to(out, input_root=inp, asset_class=h.asset_class, valuation_date=h.vdate)

    assert r1.run_id != r2.run_id
    gov = h.pricer_dir(out) / "governance"
    assert h.files_under(gov) == sorted([f"{iso}/governance_{r1.run_id}.json",
                                         f"{iso}/governance_{r2.run_id}.json"])
    assert h.files_under(other) == []
    assert h.tree(inp) == before


# --------------------------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_c5_app_serves_results_of_its_output_root(h):
    # oracle: Requirement 3 by hand: after run_to stores TRADE_1 for 2026-10-05 under output_root, an
    #   app on that output root lists exactly TRADE_1 for that date; an app on another, empty output
    #   root lists no trades; input_root is byte-identical before and after.
    inp, out, empty = h.make_input(), h.make_output(), h.make_output()
    iso = h.vdate.isoformat()
    before = h.tree(inp)
    rows = run_to(out, input_root=inp, trade_id="TRADE_1", valuation_date=h.vdate)
    assert [(r["trade_id"], r["status"]) for r in rows] == [("TRADE_1", "ok")]

    assert _served_trade_ids(out, inp, iso) == ["TRADE_1"]
    assert _served_trade_ids(empty, inp, iso) == []
    assert h.tree(inp) == before


@pytest.mark.criterion("C5")
@settings(max_examples=2)
@given(trade_id=st.sampled_from(["TRADE_1", "TRADE_2"]))
def test_c5_app_serves_exactly_the_trade_that_was_run(h, trade_id):
    # oracle: Requirement 3 (invariant): the app on an output root serves exactly the trades run into
    #   that root for the date, whichever fixture trade it was; an empty output root serves none.
    inp, out, empty = h.make_input(), h.make_output(), h.make_output()
    iso = h.vdate.isoformat()
    before = h.tree(inp)
    run_to(out, input_root=inp, trade_id=trade_id, valuation_date=h.vdate)

    assert _served_trade_ids(out, inp, iso) == [trade_id]
    assert _served_trade_ids(empty, inp, iso) == []
    assert h.tree(inp) == before
