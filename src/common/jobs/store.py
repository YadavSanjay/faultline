"""Job status/log records, via the dual-mode storage layer.

Records live under ``<data>/pricer/jobs/`` — resolved through ``src.common.storage`` so the
*submitter* (the BFF, on the edge tier) and the *executor* (the backend) read and write the same
records whether that is a local directory (local mode) or an ``s3://`` prefix (AWS mode). This is
what makes a job launched on one tier observable on the other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.common import storage


class JobRecordStore:
    def __init__(self, project_root: Path | str | None = None):
        self.jobs_dir = storage.join(storage.data_base(project_root), "pricer", "jobs")

    def rec_uri(self, job_id: str) -> str:
        return storage.join(self.jobs_dir, f"{job_id}.json")

    def log_uri(self, job_id: str) -> str:
        return storage.join(self.jobs_dir, f"{job_id}.log")

    def write(self, rec: dict[str, Any]) -> None:
        storage.write_text_atomic(self.rec_uri(rec["job_id"]), json.dumps(rec))

    def read(self, job_id: str) -> dict[str, Any] | None:
        uri = self.rec_uri(job_id)
        if not storage.exists(uri):
            return None
        try:
            return dict(json.loads(storage.read_text(uri)))
        except Exception:
            return None

    def recent(self, limit: int = 1000) -> list[dict[str, Any]]:
        recs: list[dict[str, Any]] = []
        for loc in storage.glob(self.jobs_dir, "*.json"):
            try:
                recs.append(dict(json.loads(storage.read_text(loc))))
            except Exception:
                continue
        recs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        return recs[:limit]
