"""Post-ingest data-quality gate (item 5).

Structural problems (missing file, zero rows, missing required columns) must BLOCK; staleness
and null fraction must WARN (record, don't fail the pipeline).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from src.common import storage
from src.common.quality.gate import DataQualityGate, DqGateConfig, FileRule


def _write(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _good_frame() -> pd.DataFrame:
    return pd.DataFrame({"date": ["2026-05-27", "2026-05-28", "2026-05-29"],
                         "close": [1.0, 2.0, 3.0]})


def _cfg(**files: FileRule) -> DqGateConfig:
    return DqGateConfig(max_staleness_days=7, max_null_fraction=0.5, files=dict(files))


def _by(results, check):
    return next(r for r in results if r.check == check)


def test_clean_file_passes(tmp_path: Path):
    _write(tmp_path / "a.csv", _good_frame())
    cfg = _cfg(**{"a.csv": FileRule(required_columns=["date", "close"])})
    gate = DataQualityGate(tmp_path, cfg, as_of=date(2026, 5, 29))
    results = gate.run()
    assert gate.passed(results)
    assert all(r.severity == "ok" for r in results)


def test_missing_file_blocks(tmp_path: Path):
    cfg = _cfg(**{"nope.csv": FileRule()})
    results = DataQualityGate(tmp_path, cfg, as_of=date(2026, 5, 29)).run()
    assert not DataQualityGate(tmp_path, cfg).passed(results)
    assert _by(results, "present").severity == "block"


def test_zero_rows_blocks(tmp_path: Path):
    _write(tmp_path / "a.csv", pd.DataFrame({"date": [], "close": []}))
    cfg = _cfg(**{"a.csv": FileRule()})
    results = DataQualityGate(tmp_path, cfg, as_of=date(2026, 5, 29)).run()
    assert _by(results, "min_rows").severity == "block"


def test_missing_required_column_blocks(tmp_path: Path):
    _write(tmp_path / "a.csv", _good_frame())
    cfg = _cfg(**{"a.csv": FileRule(required_columns=["date", "missing_col"])})
    results = DataQualityGate(tmp_path, cfg, as_of=date(2026, 5, 29)).run()
    assert _by(results, "required_columns").severity == "block"


def test_stale_data_warns_not_blocks(tmp_path: Path):
    _write(tmp_path / "a.csv", _good_frame())   # latest 2026-05-29
    cfg = _cfg(**{"a.csv": FileRule()})
    results = DataQualityGate(tmp_path, cfg, as_of=date(2026, 7, 1)).run()  # ~33 days later
    assert DataQualityGate(tmp_path, cfg).passed(results)  # warn doesn't fail the gate
    assert _by(results, "staleness").severity == "warn"


def test_high_null_fraction_warns(tmp_path: Path):
    df = pd.DataFrame({"date": ["2026-05-27", "2026-05-28", "2026-05-29", "2026-05-30"],
                       "close": [1.0, None, None, None]})  # 75% null > 50%
    _write(tmp_path / "a.csv", df)
    cfg = _cfg(**{"a.csv": FileRule(required_columns=["date", "close"])})
    results = DataQualityGate(tmp_path, cfg, as_of=date(2026, 5, 30)).run()
    assert DataQualityGate(tmp_path, cfg).passed(results)  # null is warn, not block
    assert _by(results, "null_fraction").severity == "warn"


# --------------------------------------------------------------------------- #
# dual-mode: the gate must police a REMOTE tree too (AWS mode), not just a path
# --------------------------------------------------------------------------- #
def test_gate_checks_a_remote_base_dir():
    """In AWS mode `base_dir` is an ``s3://…`` prefix, not a filesystem path.

    Exercised over fsspec ``memory://`` so no AWS/s3fs is needed. Before the storage-facade
    fix the gate used ``Path(base_dir) / rel`` + ``is_file()`` and reported every declared
    file as missing whenever the data tree was remote.
    """
    base = "memory://qlp-dq/marketdata"
    storage.write_text_atomic(
        storage.join(base, "a.csv"),
        "date,close\n2026-05-27,1.0\n2026-05-28,2.0\n2026-05-29,3.0\n")

    cfg = _cfg(**{"a.csv": FileRule(required_columns=["date", "close"])})
    gate = DataQualityGate(base, cfg, as_of=date(2026, 5, 29))
    results = gate.run()

    assert gate.passed(results)
    assert all(r.severity == "ok" for r in results)
    assert _by(results, "present").severity == "ok"


def test_gate_flags_a_missing_file_on_a_remote_base_dir():
    cfg = _cfg(**{"nope.csv": FileRule(required_columns=["date"])})
    results = DataQualityGate("memory://qlp-dq/empty", cfg, as_of=date(2026, 5, 29)).run()
    assert _by(results, "present").message == "file is missing"
