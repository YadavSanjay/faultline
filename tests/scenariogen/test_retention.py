"""Store retention / GC (item 8): age + count pruning, dry-run safety."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.common.store.retention import prune_by_age, prune_by_count

NOW = datetime(2026, 6, 21, tzinfo=UTC)


def _touch(path: Path, mtime_epoch: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime_epoch, mtime_epoch))


def test_prune_by_age_dry_run_keeps_files(tmp_path: Path):
    _touch(tmp_path / "old.parquet", (NOW - timedelta(days=400)).timestamp())
    _touch(tmp_path / "new.parquet", (NOW - timedelta(days=10)).timestamp())
    r = prune_by_age(tmp_path, keep_days=365, pattern="**/*.parquet", as_of=NOW, dry_run=True)
    assert r.scanned == 2 and r.removed == 1            # one is past the window
    assert (tmp_path / "old.parquet").exists()          # but dry-run deleted nothing


def test_prune_by_age_apply_removes_only_old(tmp_path: Path):
    _touch(tmp_path / "old.parquet", (NOW - timedelta(days=400)).timestamp())
    _touch(tmp_path / "new.parquet", (NOW - timedelta(days=10)).timestamp())
    r = prune_by_age(tmp_path, keep_days=365, pattern="**/*.parquet", as_of=NOW, dry_run=False)
    assert r.removed == 1
    assert not (tmp_path / "old.parquet").exists()
    assert (tmp_path / "new.parquet").exists()


def test_prune_by_count_keeps_newest(tmp_path: Path):
    base = NOW.timestamp()
    for i in range(5):
        _touch(tmp_path / f"f{i}.parquet", base + i)    # f4 newest
    r = prune_by_count(tmp_path, keep=2, pattern="**/*.parquet", dry_run=False)
    assert r.removed == 3
    assert sorted(p.name for p in tmp_path.glob("*.parquet")) == ["f3.parquet", "f4.parquet"]


def test_prune_missing_root_is_noop(tmp_path: Path):
    r = prune_by_age(tmp_path / "does_not_exist", keep_days=1, as_of=NOW)
    assert r.scanned == 0 and r.removed == 0
