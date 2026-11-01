"""Path / URI resolution for the dual-mode storage layer.

One source of truth for two things every subsystem needs:

* ``repo_root()`` — the repository root (replaces ~25 scattered ``Path(__file__).parents[n]``
  walks). Always a local ``Path`` — the *code* + *configs* are always on the local filesystem
  (baked into the image); only the *data* tree moves to S3.
* ``data_root()`` — the root of the ``data/`` tree as a location string. Local (the default) →
  the on-disk ``<repo>/data`` path, so paths are byte-for-byte what they were before. AWS →
  whatever ``PRICER_DATA_URI`` points at, e.g. ``s3://bucket/prefix``.

``join()`` composes a child location under a root, dispatching on whether the root is a remote URI
(``scheme://…`` → posix-joined) or a local path (``os.path`` semantics). Everything downstream
treats the result as an opaque location string handed to the helpers in ``fs`` / ``parquet``.
"""

from __future__ import annotations

import os
import posixpath
import re
from functools import lru_cache
from pathlib import Path

from .mode import is_aws

# A URI scheme prefix like ``s3://`` / ``gs://`` / ``file://``. A bare Windows drive (``d:\``) is
# NOT a scheme — it needs a following ``//`` to match, which drive paths never have.
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def is_remote_uri(location: str) -> bool:
    """True if ``location`` is a ``scheme://…`` URI (S3 etc.), not a local path."""
    return bool(_SCHEME_RE.match(location))


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Repository root — the directory containing ``pyproject.toml``.

    Walk up from this file looking for the ``pyproject.toml`` marker; fall back to the known
    depth (``src/common/storage/paths.py`` → 3 levels up) if the marker is somehow absent.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[3]


def data_root() -> str:
    """Root location of the ``data/`` tree as a string (local path or remote URI).

    * ``PRICER_DATA_URI`` set → used verbatim (trailing slash trimmed). This is how AWS points the
      whole tree at ``s3://bucket/prefix``; it also lets a local run relocate ``data/`` if desired.
    * unset → ``<repo>/data`` (local, historical behavior). In AWS mode with no URI this is almost
      certainly a misconfiguration, so we fail loud rather than silently write to the container FS.
    """
    uri = os.environ.get("PRICER_DATA_URI")
    if uri:
        return uri.rstrip("/")
    if is_aws():
        raise RuntimeError(
            "PRICER_DEPLOY_MODE=aws but PRICER_DATA_URI is unset — set it to the S3 data root, "
            "e.g. s3://my-bucket/qlp-data"
        )
    return str(repo_root() / "data")


def join(root: str, *parts: str) -> str:
    """Join ``parts`` under ``root``, dispatching on local-path vs remote-URI semantics."""
    if is_remote_uri(root):
        clean = [p.strip("/") for p in parts if p]
        return posixpath.join(root.rstrip("/"), *clean) if clean else root.rstrip("/")
    return str(Path(root, *parts))


def data_path(*parts: str) -> str:
    """Shorthand for ``join(data_root(), *parts)`` — a location under the data tree."""
    return join(data_root(), *parts)


def data_base(local_root: str | Path | None = None) -> str:
    """The data-tree base for a store/writer that historically took an explicit ``project_root``.

    Threading rule (keeps local — including tests that pass a temp ``project_root`` — byte-identical
    while enabling S3):

    * ``PRICER_DATA_URI`` set (or AWS mode) → the configured data root (e.g. ``s3://…``).
    * otherwise → ``<local_root>/data`` if a root was supplied, else ``<repo>/data``.

    So production/AWS follows ``data_root()``, and a local caller that passes ``tmp_path`` still
    writes under that temp dir exactly as before.
    """
    if os.environ.get("PRICER_DATA_URI") or is_aws():
        return data_root()
    root = Path(local_root) if local_root is not None else repo_root()
    return str(root / "data")
