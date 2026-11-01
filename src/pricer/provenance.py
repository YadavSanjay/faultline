"""Run + result provenance — moved to `src/common/provenance.py`.

Kept as a re-export shim so existing `pricer.provenance` imports keep working; remove once
all call sites import from `common.provenance` directly.
"""

from __future__ import annotations

from src.common.provenance import RunProvenance, config_hash, git_sha

__all__ = ["RunProvenance", "config_hash", "git_sha"]
