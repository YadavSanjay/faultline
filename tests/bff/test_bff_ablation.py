"""BFF layer-ablation, signal-health and replay-definition wiring (ablation-waterfall-view, step 15f).

Synthetic stress / ablation reports and a two-scenario ``scenarios.json`` (one replay with the raw
placeholder horizon, one authored) are written under the ``bff_root`` temp root; the routes are hit
through ``TestClient`` so the route order (before ``/api/scenarios/{name}``) is covered too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from ui.bff.app import scenarios
from ui.bff.app.main import app

DATE = "2026-10-06"
LAYERS = ["macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state"]


def _write_config(root: Path) -> None:
    d = root / "configs" / "scenariogen"
    d.mkdir(parents=True, exist_ok=True)
    (d / "scenarios.json").write_text(json.dumps({
        "severity_tiers": {"mild": 0.5, "adverse": 1.0, "severe": 1.5},
        "scenarios": {
            "ai_deflation": {"narrative": "AI deflation replay", "drivers": {}, "overlays": {},
                             "horizon_days": 10, "replay": {"start": "2021-11-29", "end": "2022-10-14"}},
            "taiwan": {"narrative": "Taiwan crisis", "horizon_days": 5, "overlays": {},
                       "drivers": {"fx:TWD": {"move_pct": 8}, "ir:SOFR": {"move_bp": 25}}},
        },
    }), encoding="utf-8")


def _scen_dir(root: Path, date: str = DATE) -> Path:
    d = root / "data" / "pricer" / "scenario" / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _entry(name: str, severity: str, *, replay: bool) -> dict[str, Any]:
    prov: dict[str, Any] = {"method": "conditional", "severity_scale": 1.0,
                            "horizon_days": 221 if replay else 5,
                            "drivers": {"eq:NVDA": -0.4} if replay else {"fx:TWD": 0.08},
                            "overlays": {}}
    if replay:
        prov["replay"] = {"start": "2021-11-29", "end": "2022-10-14", "horizon_days": 221, "n_observed": 1,
                          "excluded": {"eq:SMCI": {"move": -0.7, "reason": "accounting"}}}
    return {"scenario": name, "severity": severity, "source": "static", "scenario_version": f"v-{name}-{severity}",
            "total_pnl": -1.0e6, "materialized": {"vector": {}, "provenance": prov}, "trades": []}


def _write_stress(root: Path, run_id: str, generated_at: str | None, entries: list[dict[str, Any]],
                  **extra: Any) -> None:
    rep: dict[str, Any] = {"valuation_date": DATE, "run_id": run_id, "snapshot_id": "snap",
                           "scenarios": entries, **extra}
    if generated_at is not None:
        rep["generated_at"] = generated_at
    (_scen_dir(root) / f"scenario_{run_id}.json").write_text(json.dumps(rep), encoding="utf-8")


def _record(name: str, severity: str, rule: str = "dualrag-v3") -> dict[str, Any]:
    totals = [-10.0, -14.0, -13.0, -15.0, -19.0, -18.0, -18.5]
    ladder = [{"layer": "authored", "layers": [], "total_pnl": totals[0]}]
    ladder += [{"layer": lay, "layers": sorted(LAYERS[: i + 1]), "total_pnl": totals[i + 1]}
               for i, lay in enumerate(LAYERS)]
    return {"scenario": name, "severity": severity, "composition_rule": rule, "order": list(LAYERS),
            "ladder": ladder, "shapley": {"macrorag": -4.0, "news": -4.0, "state": -0.5},
            "subsets": [{"layers": [], "floor_hits": []}, {"layers": ["macrorag"], "floor_hits": ["TSM"]}]}


def _write_ablation(root: Path, run_id: str, severity: str, generated_at: str, records: list[dict[str, Any]],
                    date: str = DATE, layer_cost: dict[str, Any] | None = None) -> None:
    rep = {"valuation_date": date, "run_id": run_id, "generated_at": generated_at, "severity": severity,
           "composition_rule": "dualrag-v1", "note": "influence, not value", "scenarios": records,
           "stopped": [], "layer_cost": layer_cost}
    (_scen_dir(root, date) / f"ablation_{run_id}.json").write_text(json.dumps(rep), encoding="utf-8")


def _write_version(root: Path, version: str, spec: dict[str, Any]) -> None:
    d = root / "data" / "pricer" / "store" / "scenario" / "versions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{version}.json").write_text(json.dumps({"scenario_version": version, **spec}), encoding="utf-8")


# --- replay fix: catalog horizon + outcome ---------------------------------- #
def test_catalog_horizon_from_report_and_outcomes(bff_root: Path) -> None:
    _write_config(bff_root)
    _write_stress(bff_root, "r1", "2026-10-18T09:00:00",
                  [_entry("ai_deflation", "adverse", replay=True), _entry("taiwan", "adverse", replay=False)],
                  held_out=[{"scenario": "ai_deflation", "severity": "severe", "scenario_version": "v1",
                             "findings": []}],
                  stopped=[{"scenario": "taiwan", "reason": "macro_unavailable", "detail": "timeout"}])
    cells = {(c["scenario"], c["severity"]): c for c in scenarios.catalog(None)["cells"]}
    # ran replay -> the report's resolved horizon, not the raw placeholder 10
    assert cells[("ai_deflation", "adverse")]["horizon_days"] == 221
    assert cells[("ai_deflation", "adverse")]["outcome"] == "ran"
    # replay tier not run -> unresolved, no placeholder horizon
    assert cells[("ai_deflation", "mild")]["horizon_days"] is None
    assert cells[("ai_deflation", "mild")]["outcome"] == "not_run"
    assert cells[("ai_deflation", "severe")]["outcome"] == "held_out"
    # authored scenario -> config horizon when not run; stopped scenario tiers say so
    assert cells[("taiwan", "mild")]["horizon_days"] == 5
    assert cells[("taiwan", "mild")]["outcome"] == "stopped"
    assert cells[("taiwan", "adverse")]["outcome"] == "ran"


def test_detail_held_out_replay_reads_the_version_store(bff_root: Path) -> None:
    _write_config(bff_root)
    _write_version(bff_root, "vheld01", {"name": "ai_deflation", "drivers": {"eq:NVDA": {"move": -0.4}},
                                         "overlays": {}, "horizon_days": 221, "method": "conditional",
                                         "replay": {"start": "2021-11-29", "end": "2022-10-14"}})
    findings = [{"check": "beyond_hist", "severity": "fail", "factor": "eq:NVDA"},
                {"check": "corr", "severity": "flag", "factor": "eq:AMD"}]
    _write_stress(bff_root, "r1", "2026-10-18T09:00:00", [_entry("taiwan", "adverse", replay=False)],
                  held_out=[{"scenario": "ai_deflation", "severity": "severe", "scenario_version": "vheld01",
                             "status": "fail", "findings": findings}])
    d = TestClient(app).get("/api/scenarios/ai_deflation", params={"severity": "severe"}).json()
    assert d["available"] is False
    assert d["outcome"] == "held_out"
    assert "held out" in d["reason"] and "1 fail, 1 flag" in d["reason"]
    assert d["findings"] == findings
    assert d["definition"]["source"] == "version"
    assert d["definition"]["drivers"] == {"eq:NVDA": -0.4}
    assert d["horizon_days"] == 221
    assert d["drivers"]["eq:NVDA"] == {"unit": "%", "value": -40.0}
    assert d["replay"] == {"start": "2021-11-29", "end": "2022-10-14"}


def test_detail_stopped_and_not_run(bff_root: Path) -> None:
    _write_config(bff_root)
    _write_stress(bff_root, "r1", "2026-10-18T09:00:00", [_entry("taiwan", "adverse", replay=False)],
                  stopped=[{"scenario": "ai_deflation", "reason": "replay_uncovered", "detail": "no SPX"}])
    stopped = scenarios.detail("ai_deflation", "adverse", None)
    assert stopped["outcome"] == "stopped"
    assert stopped["reason"] == "stopped: replay_uncovered (no SPX)"
    assert stopped["definition"]["source"] == "unresolved"
    assert stopped["horizon_days"] is None and stopped["drivers"] == {}

    not_run = scenarios.detail("taiwan", "severe", None)
    assert not_run["outcome"] == "not_run"
    assert not_run["definition"]["source"] == "config"
    assert not_run["definition"]["drivers"] == {"fx:TWD": 0.08, "ir:SOFR": 0.0025}
    assert not_run["drivers"]["ir:SOFR"] == {"unit": "bp", "value": 25.0}


def test_detail_ran_replay_carries_the_window(bff_root: Path) -> None:
    _write_config(bff_root)
    _write_stress(bff_root, "r1", "2026-10-18T09:00:00", [_entry("ai_deflation", "adverse", replay=True)])
    d = scenarios.detail("ai_deflation", "adverse", None)
    assert d["available"] is True and d["outcome"] == "ran"
    assert d["replay"]["n_observed"] == 1
    assert d["replay"]["excluded"]["eq:SMCI"]["reason"] == "accounting"


# --- ablation endpoints ----------------------------------------------------- #
def test_ablation_view_newest_run_with_severity_and_run_id(bff_root: Path) -> None:
    _write_config(bff_root)
    rows = [{"scenario": "taiwan", "severity": "mild", "layer": "macrorag", "n_calls": 2, "latency_s": 1.0,
             "cost_usd": 0.5}]
    _write_ablation(bff_root, "a-old", "mild", "2026-10-18T08:00:00", [_record("taiwan", "mild")],
                    layer_cost={"rows": rows, "overhead": {"n_calls": 1}})
    _write_ablation(bff_root, "a-new", "severe", "2026-10-18T10:00:00",
                    [_record("taiwan", "severe"), _record("ai_bust", "severe", "dualrag-v1")])
    client = TestClient(app)

    out = client.get("/api/scenarios/ablation").json()
    assert out["available"] is True
    assert out["run_id"] == "a-new"
    assert out["valuation_date"] == DATE and out["available_dates"] == [DATE]
    assert [r["run_id"] for r in out["runs"]] == ["a-new", "a-old"]
    assert out["mixed_rules"] is True and out["composition_rules"] == ["dualrag-v1", "dualrag-v3"]
    steps = out["scenarios"][0]["steps"]
    assert [s["step"] for s in steps] == ["macrorag", "xbrlrag", "events", "market_intelligence"]
    assert steps[0]["delta"] == -4.0 and steps[0]["floor_hits"] == ["TSM"]
    assert steps[0]["cost_usd"] is None and out["overhead"] is None

    mild = client.get("/api/scenarios/ablation", params={"severity": "mild"}).json()
    assert mild["run_id"] == "a-old"
    assert mild["scenarios"][0]["steps"][0]["n_calls"] == 2
    assert mild["scenarios"][0]["steps"][0]["contribution_per_usd"] == 8.0
    assert client.get("/api/scenarios/ablation", params={"run_id": "a-old"}).json()["run_id"] == "a-old"
    missing = client.get("/api/scenarios/ablation", params={"run_id": "zz"}).json()
    assert missing["available"] is False and "zz" in missing["reason"]


def test_ablation_unavailable_without_runs_or_on_invalid_report(bff_root: Path) -> None:
    _write_config(bff_root)
    client = TestClient(app)
    none = client.get("/api/scenarios/ablation").json()
    assert none["available"] is False and none["reason"]

    bad = _record("taiwan", "severe")
    bad["ladder"][0]["layer"] = "base"
    _write_ablation(bff_root, "a-bad", "severe", "2026-10-18T10:00:00", [bad])
    out = client.get("/api/scenarios/ablation").json()
    assert out["available"] is False
    assert "authored" in out["reason"]


def test_ablation_scenario_route(bff_root: Path) -> None:
    _write_config(bff_root)
    _write_ablation(bff_root, "a1", "severe", "2026-10-18T10:00:00", [_record("taiwan", "severe")])
    client = TestClient(app)
    one = client.get("/api/scenarios/ablation/taiwan").json()
    assert one["available"] is True and one["scenario"] == "taiwan"
    assert one["waterfall"]["final_pnl"] == -18.5
    assert one["run_id"] == "a1" and "scenarios" not in one
    other = client.get("/api/scenarios/ablation/nope").json()
    assert other["available"] is False and "nope" in other["reason"]


def test_available_dates_include_ablation_only_dates(bff_root: Path) -> None:
    _write_config(bff_root)
    _write_stress(bff_root, "r1", "2026-10-18T09:00:00", [])
    _write_ablation(bff_root, "a1", "severe", "2026-10-19T10:00:00", [_record("taiwan", "severe")],
                    date="2026-10-07")
    assert scenarios.available_dates() == ["2026-10-07", DATE]


# --- signal health ---------------------------------------------------------- #
def test_signal_health_prefers_the_report_with_the_block(bff_root: Path) -> None:
    _write_config(bff_root)
    health = {"overall": "degraded", "counts": {"degraded": 1},
              "sources": [{"source": "options", "status": "degraded", "holds": {"stale": 2}}]}
    _write_stress(bff_root, "r-health", "2026-10-18T09:00:00", [], signal_health=health,
                  held_out=[{"scenario": "taiwan", "severity": "severe",
                             "findings": [{"check": "c", "severity": "fail"}]}])
    _write_stress(bff_root, "r-newer", "2026-10-18T11:00:00", [], signal_health=None)
    out = TestClient(app).get("/api/scenarios/signal-health").json()
    assert out["available"] is True and out["run_id"] == "r-health"
    assert out["overall"] == "degraded"
    assert out["sources"][0]["n_holds"] == 2
    assert out["held_out"][0]["n_fail"] == 1
    assert out["attention"] == 2


def test_signal_health_falls_back_to_the_newest_report(bff_root: Path) -> None:
    _write_config(bff_root)
    _write_stress(bff_root, "r-old", "2026-10-18T09:00:00", [])
    _write_stress(bff_root, "r-new", "2026-10-18T10:00:00", [],
                  stopped=[{"scenario": "gulf", "reason": "macro_unavailable", "detail": ""}])
    out = scenarios.signal_health(None)
    assert out["available"] is False and out["run_id"] == "r-new"
    assert out["overall"] is None and out["attention"] == 1
    assert out["stopped"][0]["scenario"] == "gulf"


def test_signal_health_without_reports(bff_root: Path) -> None:
    out = TestClient(app).get("/api/scenarios/signal-health").json()
    assert out["available"] is False
    assert out["attention"] == 0 and out["sources"] == [] and out["reason"]
