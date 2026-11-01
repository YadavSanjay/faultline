"""Dual-mode storage layer — one seam between local-filesystem and cloud (S3) data access.

Selected by ``PRICER_DEPLOY_MODE`` (``local`` default | ``aws``) with the data root at
``PRICER_DATA_URI`` (default ``<repo>/data``). Local mode reproduces the pre-existing on-disk
behavior byte-for-byte; AWS mode routes the same reads/writes at an ``s3://`` prefix.

Import the helpers you need:

    from src.common import storage
    loc = storage.data_path("pricer", "store", "results")   # location string (path or s3:// uri)
    storage.write_text_atomic(loc + "/x.json", text)
    df = storage.query(sql, loc)                              # DuckDB over parquet, either backend

See ``mode``/``paths``/``fs``/``parquet`` for the individual pieces.
"""

from __future__ import annotations

from .fs import (
    exists,
    glob,
    has_any,
    makedirs,
    makedirs_for,
    read_bytes,
    read_csv,
    read_text,
    remove,
    set_storage_options,
    sha256,
    stat,
    storage_options,
    write_bytes_atomic,
    write_text_atomic,
)
from .mode import deploy_mode, is_aws, is_local
from .parquet import query, write_dataset
from .paths import data_base, data_path, data_root, is_remote_uri, join, repo_root

__all__ = [
    "data_base",
    "data_path",
    "data_root",
    "deploy_mode",
    "exists",
    "glob",
    "has_any",
    "is_aws",
    "is_local",
    "is_remote_uri",
    "join",
    "makedirs",
    "makedirs_for",
    "query",
    "read_bytes",
    "read_csv",
    "read_text",
    "remove",
    "repo_root",
    "set_storage_options",
    "sha256",
    "stat",
    "storage_options",
    "write_bytes_atomic",
    "write_dataset",
    "write_text_atomic",
]
