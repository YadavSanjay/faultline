"""Content-addressed file snapshots — generic fingerprinting shared across modules.

Fingerprints a declared set of input files (per-file SHA-256 + size + mtime) and derives a
content-addressed `snapshot_id`: two runs over the same bytes get the same id; any change to
any input changes it. Used to pin and audit the exact data a run consumed —
`pricer`'s market-data snapshot and the `risk`/`scenariogen` factor-history snapshot both
build on these primitives. The id algorithm is fixed (sorted relpath + sha digest, 16 hex);
do not change it without re-baselining everything that records an id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.common import storage

_CHUNK = 1 << 20  # 1 MiB

# A snapshot location: a local ``Path`` (historical) or a location string / ``s3://`` URI.
Loc = str | Path


@dataclass(frozen=True)
class FileFingerprint:
    relpath: str  # posix, relative to the snapshot base dir
    sha256: str
    size_bytes: int
    mtime: str  # ISO-8601 UTC

    def as_dict(self) -> dict:
        return {
            "path": self.relpath,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
        }


def sha256_file(path: Loc) -> str:
    return storage.sha256(str(path))


def fingerprint_file(path: Loc, relpath: str) -> FileFingerprint:
    size, mtime = storage.stat(str(path))
    mtime_iso = datetime.fromtimestamp(mtime, tz=UTC).isoformat() if mtime is not None else ""
    return FileFingerprint(
        relpath=relpath,
        sha256=sha256_file(path),
        size_bytes=size,
        mtime=mtime_iso,
    )


def compute_snapshot_id(files: dict[str, FileFingerprint]) -> str:
    """Order-independent 16-hex id over (relpath, content-sha) of every fingerprinted file."""
    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(f"{rel}\x00{files[rel].sha256}\n".encode())
    return digest.hexdigest()[:16]


@dataclass
class FileSnapshot:
    """A content-addressed view of a declared set of input files under one base dir."""

    base_dir: str                       # location string (local path or s3:// uri)
    files: dict[str, FileFingerprint]   # relpath -> fingerprint (present files only)
    missing: list[str]                  # declared but absent on disk
    snapshot_id: str

    @classmethod
    def build(cls, base_dir: Loc, relpaths: list[str]) -> FileSnapshot:
        base = str(base_dir)
        files: dict[str, FileFingerprint] = {}
        missing: list[str] = []
        for rel in sorted(set(relpaths)):
            path = storage.join(base, rel)
            if storage.exists(path):
                files[rel] = fingerprint_file(path, rel)
            else:
                missing.append(rel)
        return cls(base, files, missing, compute_snapshot_id(files))

    def manifest(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": datetime.now(UTC).isoformat(),
            "base_dir": str(self.base_dir),
            "file_count": len(self.files),
            "files": [self.files[rel].as_dict() for rel in sorted(self.files)],
            "missing": self.missing,
        }

    def write_manifest(self, out_dir: Loc) -> str:
        """Atomically persist the manifest as `<out_dir>/<snapshot_id>.json`."""
        out = storage.join(str(out_dir), f"{self.snapshot_id}.json")
        storage.write_text_atomic(out, json.dumps(self.manifest(), indent=2))
        return out
