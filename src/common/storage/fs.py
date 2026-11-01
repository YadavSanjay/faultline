"""Filesystem helpers that work over both local paths and remote (S3) URIs.

Every helper dispatches on ``paths.is_remote_uri(location)``:

* **local** — the exact ``pathlib`` / ``os`` calls the code used before this layer existed, so
  local output is byte-for-byte unchanged (the golden-PV suite is the regression gate). In
  particular ``write_text_atomic`` keeps the tmp-file + ``fsync`` + ``os.replace`` idiom.
* **remote** — ``fsspec`` (imported lazily, only when a remote URI is actually used, so a pure
  local run needs neither ``fsspec`` nor ``s3fs`` importable). Object PUTs are atomic per object,
  so ``write_text_atomic`` writes straight to the target key.

Credentials for S3 come from the ambient AWS credential chain (IAM instance profile), so
``storage_options()`` is empty by default; tests may override it.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .paths import is_remote_uri, join

if TYPE_CHECKING:
    import pandas as pd

_CHUNK = 1 << 20  # 1 MiB — matches the historical snapshot hashing chunk

# Extra kwargs handed to fsspec for remote filesystems (e.g. an endpoint override in tests).
# Empty in production: S3 auth flows through the instance-role credential chain.
_STORAGE_OPTIONS: dict[str, Any] = {}


def storage_options() -> dict[str, Any]:
    """fsspec/pandas ``storage_options`` for remote access (empty ⇒ default credential chain)."""
    return dict(_STORAGE_OPTIONS)


def set_storage_options(**opts: Any) -> None:
    """Override the remote storage options (primarily for tests / non-default endpoints)."""
    _STORAGE_OPTIONS.clear()
    _STORAGE_OPTIONS.update(opts)


def _fs(location: str) -> Any:
    """Return the fsspec filesystem for a remote URI (lazy import)."""
    import fsspec  # lazy: local runs never import fsspec

    protocol = location.split("://", 1)[0]
    return fsspec.filesystem(protocol, **storage_options())


def read_csv(location: str, **kwargs: Any) -> pd.DataFrame:
    """``pd.read_csv`` that works over both local paths and remote URIs.

    The remote branch threads ``storage_options()`` through to pandas, so a non-default endpoint
    or a test override is honored rather than silently falling back to the ambient credential
    chain. pandas rejects the kwarg for local paths, hence the branch — the local call is the
    plain one the code used before this layer existed.

    Prefer this over a bare ``pd.read_csv(location)`` anywhere the location comes from
    ``storage.join``/``data_path``: such a location is an ``s3://`` URI in AWS mode.
    """
    import pandas as pd  # lazy: keeps this module importable without pandas

    if is_remote_uri(location):
        return pd.read_csv(location, storage_options=storage_options(), **kwargs)
    return pd.read_csv(location, **kwargs)


# --------------------------------------------------------------------------- #
# existence / listing
# --------------------------------------------------------------------------- #
def exists(location: str) -> bool:
    if is_remote_uri(location):
        return bool(_fs(location).exists(location))
    return Path(location).exists()


def makedirs(location: str) -> None:
    """Ensure the *directory* at ``location`` exists. No-op for object stores (implicit dirs)."""
    if is_remote_uri(location):
        return
    Path(location).mkdir(parents=True, exist_ok=True)


def makedirs_for(file_location: str) -> None:
    """Ensure the parent directory of a *file* location exists (local only)."""
    if is_remote_uri(file_location):
        return
    Path(file_location).parent.mkdir(parents=True, exist_ok=True)


def glob(root: str, pattern: str) -> list[str]:
    """List locations under ``root`` matching a glob ``pattern`` (e.g. ``**/*.parquet``)."""
    if is_remote_uri(root):
        return list(_fs(root).glob(join(root, pattern)))
    return [str(p) for p in Path(root).glob(pattern)]


def has_any(root: str, pattern: str) -> bool:
    """Cheap existence guard: does at least one location under ``root`` match ``pattern``?"""
    if is_remote_uri(root):
        return len(_fs(root).glob(join(root, pattern))) > 0
    return any(Path(root).glob(pattern))


def remove(location: str) -> None:
    if is_remote_uri(location):
        _fs(location).rm_file(location)
        return
    Path(location).unlink()


def stat(location: str) -> tuple[int, float | None]:
    """Return ``(size_bytes, mtime_epoch_or_None)`` for a file location."""
    if is_remote_uri(location):
        info = _fs(location).info(location)
        size = int(info.get("size", 0))
        lm = info.get("LastModified")
        return size, (lm.timestamp() if hasattr(lm, "timestamp") else None)
    st = Path(location).stat()
    return st.st_size, st.st_mtime


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #
def read_text(location: str, encoding: str = "utf-8") -> str:
    if is_remote_uri(location):
        with _fs(location).open(location, "r", encoding=encoding) as fh:
            return str(fh.read())
    return Path(location).read_text(encoding=encoding)


def read_bytes(location: str) -> bytes:
    if is_remote_uri(location):
        with _fs(location).open(location, "rb") as fh:
            return bytes(fh.read())
    return Path(location).read_bytes()


def sha256(location: str) -> str:
    """Streaming SHA-256 of a file location (chunked; matches the snapshot hashing)."""
    h = hashlib.sha256()
    if is_remote_uri(location):
        with _fs(location).open(location, "rb") as fh:
            for block in iter(lambda: fh.read(_CHUNK), b""):
                h.update(block)
    else:
        with open(location, "rb") as fh:
            for block in iter(lambda: fh.read(_CHUNK), b""):
                h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# write (atomic)
# --------------------------------------------------------------------------- #
def write_text_atomic(location: str, text: str, encoding: str = "utf-8") -> None:
    """Atomically write ``text`` to ``location``.

    Local: tmp file + ``fsync`` + ``os.replace`` (a crash never leaves a half-written file).
    Remote: a single object PUT (atomic per object).
    """
    if is_remote_uri(location):
        with _fs(location).open(location, "w", encoding=encoding) as fh:
            fh.write(text)
        return
    path = Path(location)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding=encoding, newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_bytes_atomic(location: str, data: bytes) -> None:
    """Atomically write ``data`` to ``location`` (see :func:`write_text_atomic`)."""
    if is_remote_uri(location):
        with _fs(location).open(location, "wb") as fh:
            fh.write(data)
        return
    path = Path(location)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
