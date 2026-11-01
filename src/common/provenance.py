"""Run provenance capture (reproducibility & audit) — shared by every module.

Every persisted artifact (priced result, risk/scenario report, downloaded CSV) is stamped
with enough metadata to reproduce and audit it later: which code (git SHA), which library
versions, which config, when, and — once item 4 lands — which immutable market-data snapshot
produced it.

`RunProvenance.capture()` is called once per run; the run-level fields are shared by every
artifact. QuantLib is imported lazily so the lightweight market-data downloaders can capture
provenance without paying for (or requiring) the pricing stack.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def _run_git(args: list[str], root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def git_sha(root: Path) -> str:
    """Current commit SHA, suffixed `-dirty` if the working tree has changes.

    Falls back to the ``PRICER_GIT_SHA`` env var (baked into the container image at
    build time, where no .git dir exists), then ``"unknown"``. Provenance must never
    break a run.
    """
    sha = _run_git(["rev-parse", "HEAD"], root)
    if not sha:
        return os.environ.get("PRICER_GIT_SHA", "unknown")
    dirty = _run_git(["status", "--porcelain"], root)
    return f"{sha}-dirty" if dirty else sha


def config_hash(config: dict) -> str:
    """Stable short hash of the effective config (order-independent)."""
    blob = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _quantlib_version() -> str:
    """QuantLib version if installed, else "" — kept optional for non-pricing modules."""
    try:
        import QuantLib as ql

        return str(ql.__version__)
    except Exception:
        return ""


@dataclass(frozen=True)
class RunProvenance:
    """Run-level provenance, captured once at the start of a run."""

    run_id: str
    git_sha: str
    quantlib_version: str
    python_version: str
    config_hash: str
    priced_at: str  # ISO-8601 UTC
    portfolio_version: str = ""   # content-addressed positions (set once portfolio is loaded)
    data_snapshot_id: str = ""    # immutable market-data snapshot (wired in item 4)

    @classmethod
    def capture(cls, root: Path, config: dict) -> RunProvenance:
        return cls(
            run_id=str(uuid4()),
            git_sha=git_sha(root),
            quantlib_version=_quantlib_version(),
            python_version=platform.python_version(),
            config_hash=config_hash(config),
            priced_at=datetime.now(UTC).isoformat(),
        )

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "git_sha": self.git_sha,
            "quantlib_version": self.quantlib_version,
            "python_version": self.python_version,
            "config_hash": self.config_hash,
            "priced_at": self.priced_at,
            "portfolio_version": self.portfolio_version,
            "data_snapshot_id": self.data_snapshot_id,
        }

    def result_provenance(
        self,
        pricer_class: str,
        market_data_snapshot_id: str | None = None,
    ) -> dict:
        """Run-level fields plus the per-result fields, for stamping one result.

        ``market_data_snapshot_id`` is wired in item 4 (immutable snapshots); until then it
        falls back to the run-level ``data_snapshot_id`` (also empty today).
        """
        snapshot = market_data_snapshot_id if market_data_snapshot_id is not None \
            else (self.data_snapshot_id or None)
        return {
            **self.as_dict(),
            "pricer_class": pricer_class,
            "market_data_snapshot_id": snapshot,
        }
