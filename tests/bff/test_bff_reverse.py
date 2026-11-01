"""BFF reverse-stress reads (`/scenarios/reverse`, `/scenarios/reverse/{name}`)."""

from __future__ import annotations

from pathlib import Path

from bff_helpers import reverse_result, write_reverse_report
from ui.bff.app import scenarios

DATE = "2026-05-29"
TARGET = 50_000_000.0


def _seed(root: Path) -> None:
    write_reverse_report(root, DATE, "R1", TARGET, [
        reverse_result(scenario="taiwan", reachable=True, severity_scale=0.44,
                       achieved_loss=50_300_000.0, target_loss=TARGET,
                       drivers=["fx:TWD", "eq:TSM"], vector={"fx:TWD": 0.035, "eq:TSM": -0.21, "ir:DGS1MO": -0.0002}),
        reverse_result(scenario="ai_bust", reachable=True, severity_scale=0.30,
                       achieved_loss=50_300_000.0, target_loss=TARGET,
                       drivers=["eq:NVDA"], vector={"eq:NVDA": -0.30}),
        reverse_result(scenario="mild_axis", reachable=False, severity_scale=10.0,
                       achieved_loss=12_000_000.0, target_loss=TARGET,
                       drivers=["ir:DGS10"], vector={"ir:DGS10": 0.01}),
    ])


def test_reverse_unavailable_without_run(bff_root: Path) -> None:
    out = scenarios.reverse(None)
    assert out["available"] is False
    assert "reason" in out


def test_reverse_ranks_most_fragile_first(bff_root: Path) -> None:
    _seed(bff_root)
    r = scenarios.reverse(None)
    assert r["available"] is True
    assert r["target_loss"] == TARGET
    assert r["n_scenarios"] == 3
    assert r["n_reachable"] == 2

    # reachable first, lowest λ first; unreachable last
    order = [x["scenario"] for x in r["results"]]
    assert order == ["ai_bust", "taiwan", "mild_axis"]
    assert r["results"][0]["severity_scale"] == 0.30
    assert r["results"][-1]["reachable"] is False
    # n_drivers derived from the materialized driver list
    taiwan = next(x for x in r["results"] if x["scenario"] == "taiwan")
    assert taiwan["n_drivers"] == 2


def test_reverse_detail_returns_breaking_vector(bff_root: Path) -> None:
    _seed(bff_root)
    d = scenarios.reverse_detail("taiwan", None)
    assert d["available"] is True
    assert d["reachable"] is True
    assert d["severity_scale"] == 0.44
    assert d["drivers"] == ["fx:TWD", "eq:TSM"]
    assert d["vector"]["eq:TSM"] == -0.21
    assert len(d["vector"]) == 3

    missing = scenarios.reverse_detail("nope", None)
    assert missing["available"] is False
