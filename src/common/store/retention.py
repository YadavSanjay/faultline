"""Retention / garbage-collection for time-growing stores.

Two policies over the files matching a glob under a root directory:
  * `prune_by_age`   — remove files older than `keep_days`.
  * `prune_by_count` — keep the newest `keep` files (by mtime), remove the rest.

Both default to **dry-run** (report what *would* be removed without deleting), so a caller
must opt in to deletion explicitly. Only regular files are considered; empty directories are
left in place. Intended for the per-run stores (scenario results, run manifests, report
JSONs) — never the content-addressed version / sign-off stores.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("common.store.retention")


@dataclass
class PruneResult:
    root: str
    pattern: str
    scanned: int = 0
    removed: int = 0
    kept: int = 0
    bytes_removed: int = 0
    dry_run: bool = True
    removed_paths: list[str] = field(default_factory=list)

    def log(self) -> None:
        verb = "would remove" if self.dry_run else "removed"
        logger.info("retention[%s]: scanned %d, %s %d (%.1f MB), kept %d",
                    self.root, self.scanned, verb, self.removed,
                    self.bytes_removed / 1e6, self.kept)


def _files(root: Path, pattern: str) -> list[Path]:
    return [p for p in root.glob(pattern) if p.is_file()]


def _remove(result: PruneResult, victims: list[Path], dry_run: bool) -> PruneResult:
    for p in victims:
        size = p.stat().st_size
        if not dry_run:
            p.unlink()
        result.removed += 1
        result.bytes_removed += size
        result.removed_paths.append(p.as_posix())
    result.kept = result.scanned - result.removed
    result.log()
    return result


def prune_by_age(root: Path, *, keep_days: int, pattern: str = "**/*",
                 as_of: datetime | None = None, dry_run: bool = True) -> PruneResult:
    """Remove files whose mtime is older than `keep_days` before `as_of` (default: now)."""
    result = PruneResult(root.as_posix(), pattern, dry_run=dry_run)
    if not root.exists():
        return result
    as_of = as_of or datetime.now(UTC)
    cutoff = (as_of - timedelta(days=keep_days)).timestamp()
    files = _files(root, pattern)
    result.scanned = len(files)
    victims = [p for p in files if p.stat().st_mtime < cutoff]
    return _remove(result, victims, dry_run)


def prune_by_count(root: Path, *, keep: int, pattern: str = "**/*",
                   dry_run: bool = True) -> PruneResult:
    """Keep the newest `keep` files (by mtime); remove the rest."""
    result = PruneResult(root.as_posix(), pattern, dry_run=dry_run)
    if not root.exists():
        return result
    files = sorted(_files(root, pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    result.scanned = len(files)
    victims = files[max(0, keep):]
    return _remove(result, victims, dry_run)
