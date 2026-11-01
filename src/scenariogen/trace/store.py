"""Persist a run's captured call-trace as JSONL (P3).

One JSON line per ``CallRecord`` under ``data/pricer/scenario/<valuation_date>/trace/<run_id>.jsonl``
— the read source for the BFF trace endpoints (P4). Atomic tmp+fsync+rename, mirroring
``report.write_stress_report``.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.common import storage

from .capture import TraceCollector


def trace_path(valuation_date: str, run_id: str, project_root: Path) -> str:
    return storage.join(
        storage.data_base(project_root), "pricer", "scenario", valuation_date, "trace",
        f"{run_id}.jsonl")


def write_trace(collector: TraceCollector, valuation_date: str, project_root: Path) -> Path | str:
    out = trace_path(valuation_date, collector.run_id, project_root)
    text = "".join(
        json.dumps(rec.to_dict(), default=str, ensure_ascii=False) + "\n"
        for rec in collector.records)
    storage.write_text_atomic(out, text)
    return out if storage.is_remote_uri(out) else Path(out)
