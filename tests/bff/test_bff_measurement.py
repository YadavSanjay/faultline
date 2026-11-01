"""BFF measurement wiring (measurement-views): coverage, decision impact and event replay.

Synthetic coverage / decision-impact reports are written per valuation date and event-replay reports
under ``data/scenariogen/event_replay/`` in the ``bff_root`` temp root; the route is hit through
``TestClient`` so its order (before ``/api/scenarios/{name}``) is covered too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from ui.bff.app import scenarios
from ui.bff.app.main import app

DATE = "2026-10-06"


@pytest.fixture
def root(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("PRICER_DATA_URI", raising=False)   # the BFF must read the temp root only
    return bff_root


def _write(path: Path, rep: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep), encoding="utf-8")


def _coverage(stress_run_id: str, generated_at: str, coverage: float = 0.75) -> dict[str, Any]:
    return {"stress_run_id": stress_run_id, "valuation_date": DATE, "generated_at": generated_at,
            "tier": "mild", "coverage": coverage, "n_directions": 2,
            "directions": [{"index": 2, "variance_share": 0.25, "spanned_share": 0.5, "uncovered": True,
                            "top_loadings": []},
                           {"index": 1, "variance_share": 0.5, "spanned_share": 0.75, "uncovered": False,
                            "top_loadings": [{"trade_id": "T1", "loading": 0.5}]}],
            "diversity": {"participation_ratio": 1.5, "target": [1.0, 3.0], "in_target": True,
                          "losses": {"taiwan": 2.0, "ai_bust": 4.0}},
            "merge_pairs": [], "missing": ["gulf"]}


def _decision(run_id: str, generated_at: str) -> dict[str, Any]:
    prof = {"stress_budget": 10.0,
            "unconstrained": {"binding": "var_budget", "worst_scenario": "taiwan", "worst_loss": 12.0,
                              "over_budget": ["taiwan"]},
            "change": {"binding": {"from": "var_budget", "to": "stress"}, "n_trades_changed": 3, "multipliers": {}}}
    return {"run_id": run_id, "valuation_date": DATE, "generated_at": generated_at, "tier": "adverse",
            "sets": [{"kind": "stress", "label": "authored", "profiles": {"risky": prof, "core": prof}}],
            "without_each_source": {"xbrlrag": {"core": {"binding": {"from": "stress", "to": "stress"},
                                                         "multipliers": {"T1": {"from": 1.0, "to": 0.5,
                                                                                "ratio": 0.5}}}}}}


def _replay(run_id: str, generated_at: str) -> dict[str, Any]:
    def ver(nearest: str, cos: float) -> dict[str, Any]:
        return {"nearest": nearest, "scores": {nearest: {"cosine": cos}}}
    return {"run_id": run_id, "generated_at": generated_at, "limitations": ["synthetic"],
            "episodes": [{"name": "covid_2020", "as_of": "2020-02-21", "horizon_days": 20,
                          "available": ["equity_crash"], "dropped": [{"scenario": "taiwan", "reason": "gate"}],
                          "versions": {"authored": ver("equity_crash", 0.5), "enriched": ver("equity_crash", 0.75)},
                          "pit": {"regime_asof": "2020-02-14", "dropped_issuer_factors": ["CR_ORCL"]}}]}


def _scen(root: Path, date: str = DATE) -> Path:
    return root / "data" / "pricer" / "scenario" / date


def _replay_dir(root: Path) -> Path:
    return root / "data" / "scenariogen" / "event_replay"


def test_measurement_newest_reports_per_block(root: Path) -> None:
    _write(_scen(root) / "coverage_s-old.json", _coverage("s-old", "2026-10-19T08:00:00", coverage=0.5))
    _write(_scen(root) / "coverage_s-new.json", _coverage("s-new", "2026-10-19T09:00:00"))
    _write(_scen(root) / "decision_d-new.json", _decision("d-new", "2026-10-19T09:30:00"))
    _write(_scen(root) / "decision_d-old.json", _decision("d-old", "2026-10-18T09:30:00"))
    _write(_replay_dir(root) / "event_replay_r-old.json", _replay("r-old", "2026-10-18T10:00:00"))
    _write(_replay_dir(root) / "event_replay_r-new.json", _replay("r-new", "2026-10-19T10:00:00"))

    out = TestClient(app).get("/api/scenarios/measurement").json()
    assert out["valuation_date"] == DATE and out["available_dates"] == [DATE]

    cov = out["coverage"]
    assert cov["available"] is True
    assert cov["stress_run_id"] == "s-new" and cov["generated_at"] == "2026-10-19T09:00:00"
    assert cov["tier"] == "mild"
    assert cov["coverage"] == 0.75 and cov["uncovered_share"] == 0.25
    assert [d["index"] for d in cov["directions"]] == [1, 2]
    assert cov["losses"] == [{"scenario": "ai_bust", "loss": 4.0}, {"scenario": "taiwan", "loss": 2.0}]

    dec = out["decision"]
    assert dec["available"] is True and dec["run_id"] == "d-new" and dec["tier"] == "adverse"
    assert [b["book"] for b in dec["books"]] == ["core", "risky"]
    assert dec["books"][0]["n_over_budget"] == 1 and dec["books"][0]["binding_to"] == "stress"
    assert dec["without"] == [{"source": "xbrlrag", "books": [
        {"book": "core", "binding_from": "stress", "binding_to": "stress", "binding_changed": False,
         "size_ratio": 0.5}]}]

    rep = out["replay"]
    assert rep["available"] is True and rep["run_id"] == "r-new"
    assert rep["limitations"] == ["synthetic"]
    ep = rep["episodes"][0]
    assert ep["gain"] == 0.25 and ep["dropped"] == ["taiwan"] and ep["regime_asof"] == "2020-02-14"


def test_measurement_date_selection(root: Path) -> None:
    _write(_scen(root, "2026-10-05") / "coverage_s1.json", _coverage("s1", "2026-10-18T08:00:00", coverage=0.25))
    _write(_scen(root) / "decision_d1.json", _decision("d1", "2026-10-19T09:30:00"))
    client = TestClient(app)

    newest = client.get("/api/scenarios/measurement").json()
    assert newest["available_dates"] == [DATE, "2026-10-05"]
    assert newest["valuation_date"] == DATE
    assert newest["coverage"]["available"] is False and "coverage" in newest["coverage"]["reason"]
    assert newest["decision"]["available"] is True

    older = client.get("/api/scenarios/measurement", params={"valuation_date": "2026-10-05"}).json()
    assert older["valuation_date"] == "2026-10-05"
    assert older["coverage"]["available"] is True and older["coverage"]["coverage"] == 0.25
    assert older["decision"]["available"] is False

    unknown = client.get("/api/scenarios/measurement", params={"valuation_date": "1999-01-01"}).json()
    assert unknown["valuation_date"] == DATE   # an unknown date falls back to the newest, like ablation


def test_measurement_without_reports(root: Path) -> None:
    out = TestClient(app).get("/api/scenarios/measurement").json()
    assert set(out) == {"valuation_date", "available_dates", "coverage", "decision", "replay"}
    assert out["valuation_date"] is None and out["available_dates"] == []
    for block in ("coverage", "decision", "replay"):
        assert out[block]["available"] is False and out[block]["reason"]


def test_measurement_block_unavailable_when_the_view_raises(root: Path) -> None:
    bad_cov = _coverage("s1", "2026-10-19T09:00:00")
    del bad_cov["directions"]
    _write(_scen(root) / "coverage_s1.json", bad_cov)
    bad_dec = _decision("d1", "2026-10-19T09:30:00")
    bad_dec["sets"].append(dict(bad_dec["sets"][0]))
    _write(_scen(root) / "decision_d1.json", bad_dec)
    bad_rep = _replay("r1", "2026-10-19T10:00:00")
    bad_rep["episodes"] = ["not an episode"]
    _write(_replay_dir(root) / "event_replay_r1.json", bad_rep)
    (_replay_dir(root) / "event_replay_junk.json").write_text("{not json", encoding="utf-8")

    out = scenarios.measurement(None)
    assert set(out["coverage"]) == {"available", "reason"}
    assert out["coverage"]["available"] is False and "directions" in out["coverage"]["reason"]
    assert out["decision"]["available"] is False and "stress" in out["decision"]["reason"]
    assert out["replay"]["available"] is False and out["replay"]["reason"]
