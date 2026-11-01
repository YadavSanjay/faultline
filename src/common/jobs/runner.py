"""Job execution — the one behavioral fork between local and AWS deployment.

``get_job_runner()`` returns the runner for the current ``deploy_mode``:

* **LocalSubprocessRunner** (local) — launch the job as a detached subprocess on this host and
  finalise the shared record when it exits (the historical BFF behavior).
* **AwsJobRunner** (aws) — enqueue the job to SQS for a backend worker to execute (the BFF on the
  edge tier must not hold QuantLib or run compute). The worker runs the same container entrypoint
  and finalises the *shared* record store (`JobRecordStore` over S3), so the BFF still observes it.

Job *records* always flow through `JobRecordStore` (storage-backed), so status is visible across
tiers regardless of which runner executed the work.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from src.common import storage

from .store import JobRecordStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _tail(path: Path, n: int = 40) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except Exception:
        return ""


class JobRunner(Protocol):
    def launch(self, *, job_id: str, argv: list[str], cwd: str,
               store: JobRecordStore, rec: dict[str, Any]) -> None: ...


class LocalSubprocessRunner:
    """Run the job as a detached subprocess on this host; a daemon thread finalises the record."""

    def launch(self, *, job_id: str, argv: list[str], cwd: str,
               store: JobRecordStore, rec: dict[str, Any]) -> None:
        log_path = Path(store.log_uri(job_id))  # local mode: a real filesystem path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(log_path, "wb")  # noqa: SIM115 — closed by the waiter thread
        proc = subprocess.Popen(
            [sys.executable, *argv], cwd=cwd, stdout=logf, stderr=subprocess.STDOUT)
        threading.Thread(
            target=self._wait, args=(proc, logf, store, rec, log_path), daemon=True).start()

    def _wait(self, proc: subprocess.Popen[bytes], logf: Any, store: JobRecordStore,
              rec: dict[str, Any], log_path: Path) -> None:
        rc = proc.wait()
        with contextlib.suppress(Exception):
            logf.close()
        store.write({**rec, "status": "ok" if rc == 0 else "failed", "returncode": rc,
                     "finished_at": _now(), "log_tail": _tail(log_path)})


class AwsJobRunner:
    """Enqueue the job to SQS for a backend worker (queue URL from ``JOBS_QUEUE_URL``)."""

    def launch(self, *, job_id: str, argv: list[str], cwd: str,
               store: JobRecordStore, rec: dict[str, Any]) -> None:
        queue = os.environ.get("JOBS_QUEUE_URL")
        if not queue:
            raise RuntimeError(
                "AwsJobRunner requires JOBS_QUEUE_URL (the SQS queue backend workers consume)")
        import boto3  # lazy: only imported in aws mode

        boto3.client("sqs").send_message(
            QueueUrl=queue,
            MessageBody=json.dumps({"job_id": job_id, "argv": argv, "cwd": cwd}))
        store.write({**rec, "status": "queued"})


def get_job_runner() -> JobRunner:
    """The job runner for the current deploy mode (local subprocess vs AWS SQS enqueue)."""
    return AwsJobRunner() if storage.is_aws() else LocalSubprocessRunner()
