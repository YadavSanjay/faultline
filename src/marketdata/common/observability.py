"""Observability for the market-data downloaders (shared across all asset-class runners).

Routes every runner through the platform observability SDK (`src/common/obs`) so downloads
emit the same signals as the rest of the pipeline: JSON logs carrying a run_id, Prometheus
metrics (per-source outcomes + run duration), and a per-run audit manifest under
`data/marketdata/store/runs/`. See `docs/design/AITrade_production_hardening_design.md` item 1.

Per-source data provenance (downloaded_at / source_url / content hash) is a separate concern
handled in item 3; this module covers run-level operability only.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.common.obs import metrics as obs_metrics
from src.common.obs.log import bind, configure_logging
from src.common.obs.run_manifest import RunManifest, RunManifestStore
from src.common.provenance import git_sha

_ROOT = Path(__file__).resolve().parents[3]

# Shared registry; names prefixed `marketdata_`.
MD_SOURCE = obs_metrics.counter(
    "marketdata", "source_total", "Downloader source outcomes",
    ("asset_class", "source", "status"))
MD_DURATION = obs_metrics.histogram(
    "marketdata", "run_seconds", "Downloader run duration (s)", ("asset_class",),
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0))
MD_RUNS = obs_metrics.counter(
    "marketdata", "runs_total", "Downloader runs by status", ("asset_class", "status"))


def setup_logging(asset_class: str) -> None:
    """Install platform logging (JSON when PRICER_JSON_LOGS is set) for a downloader run."""
    json_logs = os.getenv("PRICER_JSON_LOGS", "").lower() in {"1", "true", "yes"}
    configure_logging(json_logs=json_logs, level=logging.INFO,
                      logfile=_ROOT / "logs" / f"{asset_class}_marketdata.log")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


@dataclass
class RunHandle:
    asset_class: str
    run_id: str
    started: datetime
    t0: float


def begin_run(asset_class: str) -> RunHandle:
    """Start a downloader run: install platform logging, allocate a run_id, bind context."""
    setup_logging(asset_class)
    run_id = uuid.uuid4().hex[:12]
    bind(run_id=run_id, module="marketdata", asset_class=asset_class)
    return RunHandle(asset_class, run_id, datetime.now(UTC), time.perf_counter())


def _classify(status: str) -> str:
    if status == "ok":
        return "ok"
    if status.startswith("skipped"):
        return "skipped"
    return "failed"


def record_run(handle: RunHandle, results: dict[str, str]) -> None:
    """Emit per-source metrics + one audit manifest row for a finished downloader run."""
    duration = time.perf_counter() - handle.t0
    buckets = {"ok": 0, "skipped": 0, "failed": 0}
    for source, status in results.items():
        cls = _classify(status)
        buckets[cls] += 1
        MD_SOURCE.labels(asset_class=handle.asset_class, source=source, status=cls).inc()

    MD_DURATION.labels(asset_class=handle.asset_class).observe(duration)
    run_status = "failed" if buckets["failed"] else "ok"
    MD_RUNS.labels(asset_class=handle.asset_class, status=run_status).inc()

    manifest = RunManifest(
        module="marketdata", run_id=handle.run_id,
        started_at=handle.started.isoformat(), finished_at=datetime.now(UTC).isoformat(),
        duration_s=duration, status=run_status,
        partition=handle.started.date().isoformat(), git_sha=git_sha(_ROOT),
        counts={"n_sources": len(results), "n_ok": buckets["ok"],
                "n_skipped": buckets["skipped"], "n_failed": buckets["failed"]},
        extra={"asset_class": handle.asset_class, **results},
    )
    RunManifestStore(_ROOT, "marketdata").write(manifest)
