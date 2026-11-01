"""Background job runner for gated 'run' writes (run-triggering + reverse-stress solve).

Engine runs are minutes long, so the BFF launches a **whitelisted** runner and tracks its status;
the UI polls. Only a fixed set of jobs can be launched, each with a fixed argv (no client-supplied
command or args), so this adds no arbitrary-exec surface beyond the runners the repo already ships.
Each launch is gated (require_actor) and audited.

Execution + record persistence are delegated to ``src.common.jobs`` so they follow ``deploy_mode``:
locally a detached subprocess writes records under ``<data>/pricer/jobs/``; in AWS the job is
enqueued to SQS for a backend worker and the record store is the shared S3 tree. This module keeps
the whitelist, the gate, and the stable BFF API (``catalog`` / ``launch`` / ``status`` / ``recent``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from src.common.jobs import JobRecordStore, get_job_runner

from .settings import project_root

# key -> {label, argv}. argv is fixed; the client only picks a key.
_WHITELIST: dict[str, dict[str, Any]] = {
    "pricer": {"label": "Pricer — book valuation", "argv": ["-m", "src.pricer.runner"]},
    "risk": {"label": "Risk — VaR / sensitivities / backtest", "argv": ["-m", "src.pricer.risk_runner"]},
    "scenariogen": {"label": "Stress scenarios", "argv": ["-m", "src.scenariogen.runner"]},
    "reverse_stress": {"label": "Reverse-stress solve", "argv": ["-m", "src.scenariogen.runner", "--reverse-stress"]},
    "sizing": {"label": "Portfolio sizing", "argv": ["-m", "src.pricer.sizing.sizing_runner"]},
    "governance": {"label": "Governance — golden / IPV", "argv": ["-m", "src.pricer.governance_runner"]},
}

_SLIM_KEYS = ("job_id", "key", "label", "actor", "note", "status",
              "started_at", "finished_at", "returncode")


def catalog() -> list[dict[str, str]]:
    return [{"key": k, "label": v["label"], "cmd": "python " + " ".join(v["argv"])}
            for k, v in _WHITELIST.items()]


def _store() -> JobRecordStore:
    return JobRecordStore(project_root())


def _now() -> str:
    return datetime.now(UTC).isoformat()


def launch(key: str, actor: str, note: str = "") -> dict[str, Any]:
    """Launch a whitelisted runner (subprocess locally / SQS-enqueued in AWS); return the record."""
    spec = _WHITELIST.get(key)
    if spec is None:
        return {"ok": False, "error": f"unknown job '{key}'"}

    job_id = uuid.uuid4().hex[:12]
    rec: dict[str, Any] = {
        "job_id": job_id, "key": key, "label": spec["label"], "actor": actor, "note": note,
        "cmd": "python " + " ".join(spec["argv"]), "status": "running",
        "started_at": _now(), "finished_at": None, "returncode": None, "log_tail": "",
    }
    store = _store()
    store.write(rec)
    get_job_runner().launch(
        job_id=job_id, argv=spec["argv"], cwd=str(project_root()), store=store, rec=rec)
    return {"ok": True, "job": rec}


def status(job_id: str) -> dict[str, Any]:
    rec = _store().read(job_id)
    if rec is None:
        return {"available": False, "job_id": job_id}
    rec["available"] = True
    return rec


def recent(limit: int = 30) -> dict[str, Any]:
    records = _store().recent()
    jobs = [{k: r.get(k) for k in _SLIM_KEYS} for r in records]
    n_running = sum(1 for j in jobs if j.get("status") == "running")
    return {"jobs": jobs[:limit], "count": len(jobs), "n_running": n_running}
