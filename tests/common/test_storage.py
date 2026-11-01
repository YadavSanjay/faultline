"""Unit tests for the dual-mode storage layer (src/common/storage).

Local behavior is exercised against a temp dir; the *remote* code branch is exercised against
fsspec's built-in ``memory://`` filesystem, so the tests need neither AWS nor ``s3fs`` installed.
The DuckDB-over-S3 execution path (which needs live httpfs/S3) is verified only at the
glob-construction level here; end-to-end S3 is a live-AWS acceptance test.
"""

from __future__ import annotations

import hashlib

import pyarrow as pa
import pytest
from src.common import storage
from src.common.storage import parquet as sp


# --------------------------------------------------------------------------- #
# mode / root / join
# --------------------------------------------------------------------------- #
def test_defaults_local(monkeypatch):
    monkeypatch.delenv("PRICER_DEPLOY_MODE", raising=False)
    monkeypatch.delenv("PRICER_DATA_URI", raising=False)
    assert storage.is_local()
    assert not storage.is_aws()
    assert storage.data_root() == str(storage.repo_root() / "data")
    assert not storage.is_remote_uri(storage.data_root())


def test_data_uri_override(monkeypatch):
    monkeypatch.setenv("PRICER_DATA_URI", "s3://bucket/prefix/")
    assert storage.data_root() == "s3://bucket/prefix"
    assert storage.is_remote_uri(storage.data_root())


def test_aws_mode_requires_data_uri(monkeypatch):
    monkeypatch.setenv("PRICER_DEPLOY_MODE", "aws")
    monkeypatch.delenv("PRICER_DATA_URI", raising=False)
    with pytest.raises(RuntimeError, match="PRICER_DATA_URI"):
        storage.data_root()


@pytest.mark.parametrize(
    ("loc", "remote"),
    [
        ("s3://b/k", True),
        ("gs://b/k", True),
        ("memory://x", True),
        ("/abs/path", False),
        ("d:\\data", False),
        ("relative/path", False),
    ],
)
def test_is_remote_uri(loc, remote):
    assert storage.is_remote_uri(loc) is remote


def test_join_remote():
    assert storage.join("s3://b/root", "pricer", "store") == "s3://b/root/pricer/store"
    assert storage.join("s3://b/root/", "a/", "/b") == "s3://b/root/a/b"


def test_join_local():
    import os

    assert storage.join("root", "a", "b") == os.path.join("root", "a", "b")


def test_data_path(monkeypatch):
    monkeypatch.setenv("PRICER_DATA_URI", "s3://b/qlp")
    assert storage.data_path("pricer", "store") == "s3://b/qlp/pricer/store"


# --------------------------------------------------------------------------- #
# read / write — local
# --------------------------------------------------------------------------- #
def test_write_read_text_atomic_local(tmp_path):
    loc = str(tmp_path / "sub" / "x.json")
    storage.write_text_atomic(loc, '{"a": 1}')
    assert storage.exists(loc)
    assert storage.read_text(loc) == '{"a": 1}'
    # no leftover temp file
    assert not (tmp_path / "sub" / ".x.json.tmp").exists()


def test_sha256_local_matches_hashlib(tmp_path):
    loc = str(tmp_path / "b.bin")
    storage.write_bytes_atomic(loc, b"hello-bytes")
    assert storage.sha256(loc) == hashlib.sha256(b"hello-bytes").hexdigest()


def test_glob_and_has_any_local(tmp_path):
    root = str(tmp_path)
    storage.write_text_atomic(str(tmp_path / "a" / "1.parquet"), "x")
    storage.write_text_atomic(str(tmp_path / "b" / "2.parquet"), "y")
    matches = storage.glob(root, "**/*.parquet")
    assert len(matches) == 2
    assert storage.has_any(root, "**/*.parquet")
    assert not storage.has_any(root, "**/*.csv")


# --------------------------------------------------------------------------- #
# read / write — remote branch via fsspec memory://
# --------------------------------------------------------------------------- #
def test_write_read_text_atomic_remote():
    loc = "memory://qlp-test/dir/x.json"
    storage.write_text_atomic(loc, "payload")
    assert storage.exists(loc)
    assert storage.read_text(loc) == "payload"


def test_sha256_remote_matches_hashlib():
    loc = "memory://qlp-test/hash/b.bin"
    storage.write_bytes_atomic(loc, b"remote-bytes")
    assert storage.sha256(loc) == hashlib.sha256(b"remote-bytes").hexdigest()


def test_has_any_remote():
    storage.write_text_atomic("memory://qlp-test/glob/a/1.parquet", "x")
    assert storage.has_any("memory://qlp-test/glob", "**/*.parquet")
    assert not storage.has_any("memory://qlp-test/glob", "**/*.csv")


# --------------------------------------------------------------------------- #
# parquet + duckdb — local round-trip; remote expr construction
# --------------------------------------------------------------------------- #
def test_parquet_write_and_query_local(tmp_path):
    dataset = str(tmp_path / "results")
    table = pa.table(
        {
            "part": ["a", "a", "b"],
            "trade_id": ["t1", "t2", "t3"],
            "pv": [1.0, 2.0, 3.0],
        }
    )
    storage.write_dataset(
        table, dataset, partition_cols=["part"], basename_template="run-{i}.parquet"
    )
    # empty guard: a different, empty dataset returns an empty frame
    assert storage.query("SELECT * FROM @SRC", str(tmp_path / "empty")).empty
    df = storage.query("SELECT trade_id, pv FROM @SRC ORDER BY trade_id", dataset)
    assert list(df["trade_id"]) == ["t1", "t2", "t3"]
    assert df["pv"].sum() == 6.0


def test_read_csv_local(tmp_path):
    loc = str(tmp_path / "x.csv")
    (tmp_path / "x.csv").write_text("date,v\n2026-01-02,1.5\n", encoding="utf-8")
    df = storage.read_csv(loc)
    assert list(df.columns) == ["date", "v"]
    assert df["v"].iloc[0] == 1.5


def test_read_csv_remote():
    """The remote branch must reach fsspec — a bare pd.read_csv would need s3fs creds wiring."""
    loc = "memory://qlp-test/csv/y.csv"
    storage.write_text_atomic(loc, "date,v\n2026-01-02,2.5\n2026-01-03,3.5\n")
    df = storage.read_csv(loc)
    assert list(df.columns) == ["date", "v"]
    assert df["v"].sum() == 6.0


def test_read_csv_passes_kwargs_through():
    loc = "memory://qlp-test/csv/z.csv"
    storage.write_text_atomic(loc, "a,b\n1,2\n3,4\n")
    assert list(storage.read_csv(loc, usecols=["b"]).columns) == ["b"]


def test_read_parquet_expr_remote_builds_s3_glob():
    expr = sp._read_parquet_expr("s3://bucket/qlp/results", recursive=True, extra=", x=1")
    assert expr == "read_parquet('s3://bucket/qlp/results/**/*.parquet', x=1)"


def test_read_parquet_expr_local_forward_slashes():
    expr = sp._read_parquet_expr("C:\\data\\results", recursive=False, extra="")
    assert "C:/data/results/*.parquet" in expr
    assert "\\" not in expr
