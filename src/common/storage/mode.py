"""Deployment mode — the single switch between local-filesystem and cloud (S3) storage.

`deploy_mode()` reads `PRICER_DEPLOY_MODE` (default `"local"`). It is deliberately read straight
from the environment (not via a subsystem `Settings` object) so the storage layer stays free of any
import cycle with the config packages that themselves resolve paths through it.

The mode governs *behavior* (atomic-write mechanism, DuckDB httpfs, filesystem selection); the data
*location* is governed separately by `PRICER_DATA_URI` (see `paths.data_root`). Local mode with an
unset `PRICER_DATA_URI` reproduces the historical `<repo>/data` behavior byte-for-byte.
"""

from __future__ import annotations

import os

_LOCAL = "local"
_AWS = "aws"


def deploy_mode() -> str:
    """Return the normalized deploy mode: ``"local"`` (default) or ``"aws"``."""
    raw = (os.environ.get("PRICER_DEPLOY_MODE") or _LOCAL).strip().lower()
    return _AWS if raw in {_AWS, "s3", "cloud"} else _LOCAL


def is_aws() -> bool:
    return deploy_mode() == _AWS


def is_local() -> bool:
    return deploy_mode() == _LOCAL
