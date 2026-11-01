"""BFF settings — project-root and upstream-service resolution.

The BFF reads the platform's data stores directly from the repository tree, so it needs
the project root. By default it resolves to the repo root (three levels above this file:
ui/bff/app/ -> ui/bff -> ui -> <root>); override with ``QLP_ROOT``. The upstream pricer
service base URL (for the health/metrics proxy) is ``QLP_SERVICE_URL``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def project_root() -> Path:
    env = os.environ.get("QLP_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[3]


def service_url() -> str:
    """Base URL of the upstream FastAPI pricer service (src/pricer/service.py)."""
    return os.environ.get("QLP_SERVICE_URL", "http://127.0.0.1:8080").rstrip("/")


def data_dir() -> Path:
    """Root of the data tree the BFF reads.

    v1 AWS: the BFF reads a local/EFS mount of the data tree — point ``QLP_ROOT`` at it, or set
    ``PRICER_DATA_URI`` to a local path (shared with the src storage layer). A remote ``s3://``
    ``PRICER_DATA_URI`` is ignored here: native S3 reads across the BFF's DuckDB layer are a
    follow-on (see docs/design/AITrade_aws_deployment_design.md §7.6); until then the BFF is fed
    from an EFS / S3-synced mount.
    """
    uri = os.environ.get("PRICER_DATA_URI")
    if uri and "://" not in uri:
        return Path(uri)
    return project_root() / "data"


def cors_origins() -> list[str]:
    """Allowed CORS origins: ``QLP_CORS_ORIGINS`` (comma-separated) in AWS, else the dev defaults."""
    env = os.environ.get("QLP_CORS_ORIGINS")
    if env:
        return [o.strip() for o in env.split(",") if o.strip()]
    return list(DEV_ORIGINS)


# CORS origins for the Vite dev server (and a couple of common alternates).
DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
]
