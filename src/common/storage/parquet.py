"""Parquet-dataset write + DuckDB-query helpers over local paths or S3 URIs.

The repeated store idiom in this codebase is: append a run's rows to a Hive-partitioned Parquet
dataset with ``pyarrow.parquet.write_to_dataset``, then read it back with a DuckDB
``read_parquet('<glob>')`` query (guarded by a "any files?" existence check). This module keeps that
idiom identical for local paths and adds the S3 branch:

* **write** — local passes ``root_path=<dir>`` as before; remote passes a pyarrow filesystem
  (fsspec-backed) and a scheme-less path.
* **query** — local interpolates the local glob as before; remote installs/loads the DuckDB
  ``httpfs`` extension, registers an S3 credential-chain secret, and interpolates the ``s3://`` glob.

DuckDB and pyarrow are already hard dependencies, so importing them at module load is fine.
"""

from __future__ import annotations

from typing import Any, cast

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import fs
from .paths import is_remote_uri, join


def _remote_pa_fs(uri: str) -> tuple[Any, str]:
    """Return ``(pyarrow_filesystem, path_without_scheme)`` for a remote dataset URI."""
    import fsspec
    from pyarrow.fs import FSSpecHandler, PyFileSystem

    protocol = uri.split("://", 1)[0]
    fsspec_fs = fsspec.filesystem(protocol, **fs.storage_options())
    path = uri.split("://", 1)[1]  # pyarrow wants "bucket/prefix", not "s3://bucket/prefix"
    return PyFileSystem(FSSpecHandler(fsspec_fs)), path


def write_dataset(
    table: pa.Table,
    dataset_uri: str,
    *,
    partition_cols: list[str],
    basename_template: str,
    existing_data_behavior: str = "overwrite_or_ignore",
) -> None:
    """Append ``table`` to the Hive-partitioned Parquet dataset at ``dataset_uri``."""
    if is_remote_uri(dataset_uri):
        pa_fs, root_path = _remote_pa_fs(dataset_uri)
        pq.write_to_dataset(
            table,
            root_path=root_path,
            filesystem=pa_fs,
            partition_cols=partition_cols,
            basename_template=basename_template,
            existing_data_behavior=existing_data_behavior,
        )
        return
    fs.makedirs(dataset_uri)
    pq.write_to_dataset(
        table,
        root_path=dataset_uri,
        partition_cols=partition_cols,
        basename_template=basename_template,
        existing_data_behavior=existing_data_behavior,
    )


def _read_parquet_expr(dataset_uri: str, *, recursive: bool, extra: str) -> str:
    """Build the DuckDB ``read_parquet('<glob>', …)`` expression for a dataset."""
    pattern = "**/*.parquet" if recursive else "*.parquet"
    glob = join(dataset_uri, pattern)
    if not is_remote_uri(dataset_uri):
        glob = str(glob).replace("\\", "/")  # DuckDB wants forward slashes even on Windows
    return f"read_parquet('{glob}'{extra})"


def query(
    sql: str,
    dataset_uri: str,
    *,
    params: list[Any] | None = None,
    recursive: bool = True,
    read_parquet_extra: str = ", hive_partitioning=true, hive_types_autocast=0",
    placeholder: str = "@SRC",
) -> pd.DataFrame:
    """Run ``sql`` (with ``placeholder`` replaced by a ``read_parquet`` source) over the dataset.

    Returns an empty frame if the dataset has no parquet files yet (matches the historical guard).
    """
    if not fs.has_any(dataset_uri, "**/*.parquet" if recursive else "*.parquet"):
        return pd.DataFrame()
    src = _read_parquet_expr(dataset_uri, recursive=recursive, extra=read_parquet_extra)
    con = duckdb.connect()
    try:
        if is_remote_uri(dataset_uri):
            con.execute("INSTALL httpfs; LOAD httpfs;")
            con.execute("CREATE SECRET IF NOT EXISTS s3cred (TYPE s3, PROVIDER credential_chain);")
        full = sql.replace(placeholder, src)
        return cast("pd.DataFrame", con.execute(full, params or []).df())
    finally:
        con.close()
