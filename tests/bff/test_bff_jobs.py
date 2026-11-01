"""BFF job runner (gated run-triggering + reverse-stress solve).

A fake Popen keeps the tests from launching a real (minutes-long) engine run.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from src.common.jobs import runner as jobrunner
from ui.bff.app import jobs


class _FakePopen:
    """Stands in for subprocess.Popen — exits immediately with a chosen return code."""

    def __init__(self, *_a, rc: int = 0, **_k) -> None:
        self._rc = rc

    def wait(self) -> int:
        return self._rc


def _await_finish(job_id: str, timeout_s: float = 3.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rec = jobs.status(job_id)
        if rec.get("status") != "running":
            return rec
        time.sleep(0.02)
    return jobs.status(job_id)


def test_catalog_lists_whitelisted_runs(bff_root: Path) -> None:
    keys = {j["key"] for j in jobs.catalog()}
    assert "reverse_stress" in keys
    assert "risk" in keys and "pricer" in keys


def test_launch_unknown_key_is_rejected(bff_root: Path) -> None:
    out = jobs.launch("bogus", "alice")
    assert out["ok"] is False
    assert jobs.recent()["count"] == 0  # nothing recorded


def test_launch_records_and_finalises_ok(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobrunner.subprocess, "Popen", lambda *a, **k: _FakePopen(*a, rc=0, **k))
    out = jobs.launch("risk", "alice", "nightly")
    assert out["ok"] is True
    job = out["job"]
    assert job["status"] == "running" and job["actor"] == "alice"

    final = _await_finish(job["job_id"])
    assert final["status"] == "ok"
    assert final["returncode"] == 0
    assert final["finished_at"]

    listing = jobs.recent()
    assert listing["count"] == 1
    assert listing["jobs"][0]["job_id"] == job["job_id"]


def test_launch_finalises_failed_on_nonzero_exit(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobrunner.subprocess, "Popen", lambda *a, **k: _FakePopen(*a, rc=1, **k))
    out = jobs.launch("pricer", "bob")
    final = _await_finish(out["job"]["job_id"])
    assert final["status"] == "failed"
    assert final["returncode"] == 1
