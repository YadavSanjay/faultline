"""Artifact builders for the BFF read-layer tests.

Kept in a uniquely-named module (not ``conftest``) so ``from bff_helpers import ...`` always
resolves to this file regardless of collection order — pytest imports every ``conftest.py``
under the same module name, so importing shared helpers from ``conftest`` is order-fragile
when the BFF tests are collected alongside another package's ``conftest``. The ``bff_root``
fixture itself stays in ``conftest.py`` (that is where pytest discovers fixtures).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_portfolio(root: Path, trades: list[dict[str, Any]]) -> None:
    """Write src/pricer/portfolio/portfolio.json under the temp root."""
    d = root / "src" / "pricer" / "portfolio"
    d.mkdir(parents=True, exist_ok=True)
    (d / "portfolio.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")


def write_results(root: Path, records: list[dict[str, Any]], run_id: str) -> None:
    """Append a run's per-trade records to the Parquet results store (via ResultStore)."""
    from src.pricer.output.result_store import ResultStore

    ResultStore(root).write_run(records, run_id)


def pv_record(*, trade_id: str, date: str, asset_class: str, pv: float,
              priced_at: str, run_id: str, trade_type: str = "stock",
              status: str = "ok", n_warnings: int = 0) -> dict[str, Any]:
    """A single results-store row (all _SCHEMA fields present)."""
    return {
        "valuation_type": "PV", "valuation_date": date, "asset_class": asset_class,
        "trade_id": trade_id, "trade_type": trade_type, "currency": "USD",
        "pv": pv, "status": status, "n_warnings": n_warnings,
        "run_id": run_id, "snapshot_id": "snap-1", "git_sha": "abc", "quantlib_version": "1.0",
        "priced_at": priced_at, "measures_json": "{}",
    }


def write_risk_report(root: Path, date: str, report: dict[str, Any],
                      run_id: str = "run-risk-1") -> None:
    d = root / "data" / "pricer" / "risk" / date
    d.mkdir(parents=True, exist_ok=True)
    (d / f"risk_{run_id}.json").write_text(json.dumps(report), encoding="utf-8")


def write_pricer_config(root: Path, limits: dict[str, Any]) -> None:
    d = root / "configs" / "pricer"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pricer_config.json").write_text(json.dumps({"risk": {"limits": limits}}), encoding="utf-8")


def write_feed_csv(root: Path, rel: str, header: list[str], rows: list[list[Any]]) -> None:
    p = root / "data" / "marketdata" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(header)] + [",".join(str(c) for c in r) for r in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_dq_gate(root: Path, files: dict[str, Any], max_staleness_days: int = 7) -> None:
    d = root / "configs" / "marketdata"
    d.mkdir(parents=True, exist_ok=True)
    (d / "dq_gate.json").write_text(
        json.dumps({"max_staleness_days": max_staleness_days, "min_rows": 1, "files": files}),
        encoding="utf-8",
    )


def write_llm_manifest(root: Path, run_id: str, manifest: dict[str, Any]) -> None:
    d = root / "data" / "pricer" / "scenario_llm" / run_id
    d.mkdir(parents=True, exist_ok=True)
    manifest.setdefault("run_id", run_id)
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def write_trace(root: Path, date: str, run_id: str, records: list[dict[str, Any]]) -> None:
    d = root / "data" / "pricer" / "scenario" / date / "trace"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run_id}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def write_llm_config(root: Path, budgets: dict[str, Any], model_tiers: dict[str, str]) -> None:
    d = root / "configs" / "scenariogen" / "llm"
    d.mkdir(parents=True, exist_ok=True)
    (d / "llm.json").write_text(
        json.dumps({"budgets": budgets, "model_tiers": model_tiers}), encoding="utf-8")


def write_rag_config(root: Path, filename: str, connection: dict[str, Any]) -> None:
    d = root / "configs" / "scenariogen"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(json.dumps({"connection": connection}), encoding="utf-8")


def write_roles_config(root: Path, cfg: dict[str, Any]) -> None:
    d = root / "configs" / "ui"
    d.mkdir(parents=True, exist_ok=True)
    (d / "roles.json").write_text(json.dumps(cfg), encoding="utf-8")


def write_reverse_report(root: Path, date: str, run_id: str, target_loss: float,
                         results: list[dict[str, Any]]) -> None:
    d = root / "data" / "pricer" / "scenario" / date
    d.mkdir(parents=True, exist_ok=True)
    (d / f"reverse_{run_id}.json").write_text(json.dumps({
        "valuation_date": date, "run_id": run_id, "snapshot_id": "snap-1",
        "generated_at": f"{date}T00:00:00", "target_loss": target_loss, "results": results,
    }), encoding="utf-8")


def reverse_result(*, scenario: str, reachable: bool, severity_scale: float,
                   achieved_loss: float, target_loss: float,
                   drivers: list[str], vector: dict[str, float]) -> dict[str, Any]:
    return {
        "scenario": scenario, "reachable": reachable, "severity_scale": severity_scale,
        "achieved_loss": achieved_loss, "target_loss": target_loss, "iterations": 10,
        "materialized": {"name": scenario, "drivers": drivers, "vector": vector},
    }


def write_sizing_report(root: Path, date: str, report: dict[str, Any]) -> None:
    d = root / "data" / "pricer" / "sizing" / date
    d.mkdir(parents=True, exist_ok=True)
    (d / "sizing_latest.json").write_text(json.dumps(report), encoding="utf-8")


def write_book_variant(root: Path, profile: str, book: dict[str, Any]) -> None:
    d = root / "src" / "pricer" / "portfolio"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"portfolio_{profile}.json").write_text(json.dumps(book), encoding="utf-8")


def write_active_portfolio(root: Path, book: dict[str, Any]) -> None:
    d = root / "src" / "pricer" / "portfolio"
    d.mkdir(parents=True, exist_ok=True)
    (d / "portfolio.json").write_text(json.dumps(book), encoding="utf-8")


def call_record(*, seq: int, service: str, purpose: str, cost_usd: float = 0.0,
                latency_ms: float = 100.0, ok: bool = True, model: str = "",
                total_tokens: int = 0, scenario: str = "s1") -> dict[str, Any]:
    return {
        "seq": seq, "service": service, "purpose": purpose, "ok": ok,
        "latency_ms": latency_ms, "scenario": scenario, "model": model,
        "prompt_tokens": total_tokens, "completion_tokens": 0, "total_tokens": total_tokens,
        "cost_usd": cost_usd, "prompt_hash": "", "system": "", "user": "",
        "request": "", "response": "", "error": "" if ok else "boom",
    }
