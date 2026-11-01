"""data_store: upsert merge/dedup + the provenance sidecar (items 3 + 6)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from src.marketdata.common import data_store


def test_upsert_creates_then_dedups(tmp_path: Path):
    path = tmp_path / "series.csv"
    data_store.upsert_csv(pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "v": [1.0, 2.0]}), path)
    # overlapping date with a new value -> last wins; new date appended
    data_store.upsert_csv(pd.DataFrame({"date": ["2026-01-02", "2026-01-03"], "v": [9.0, 3.0]}), path)

    df = pd.read_csv(path)
    assert list(df["date"]) == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert df.set_index("date").loc["2026-01-02", "v"] == 9.0  # new value won


def test_upsert_multi_key_dedup(tmp_path: Path):
    path = tmp_path / "wide.csv"
    base = pd.DataFrame({"date": ["2026-01-01", "2026-01-01"], "ticker": ["A", "B"], "v": [1.0, 2.0]})
    data_store.upsert_csv(base, path, key_cols=["date", "ticker"])
    data_store.upsert_csv(
        pd.DataFrame({"date": ["2026-01-01"], "ticker": ["A"], "v": [5.0]}),
        path, key_cols=["date", "ticker"])
    df = pd.read_csv(path)
    assert len(df) == 2  # (A,B) unchanged count
    assert df.set_index(["date", "ticker"]).loc[("2026-01-01", "A"), "v"] == 5.0


def test_save_csv_writes_provenance_sidecar(tmp_path: Path):
    path = tmp_path / "demo.csv"
    df = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "v": [1.0, 2.0]})
    data_store.save_csv(df, path)

    sidecar = path.with_suffix(".csv.provenance.json")
    assert sidecar.exists()
    prov = json.loads(sidecar.read_text(encoding="utf-8"))
    assert prov["row_count"] == 2
    assert prov["date_min"] == "2026-01-01" and prov["date_max"] == "2026-01-02"
    assert len(prov["content_sha256"]) == 64 and "git_sha" in prov


def test_empty_frame_writes_nothing(tmp_path: Path):
    path = tmp_path / "empty.csv"
    data_store.save_csv(pd.DataFrame(), path)
    assert not path.exists()


def test_upsert_same_date_repull_new_wins_at_scale(tmp_path: Path):
    # A same-day re-pull of a large snapshot (e.g. an option chain re-fetched after the close)
    # must replace every row. The tie on date_col is large enough that an unstable sort
    # scrambles existing-vs-new order, so keep="last" would silently keep the stale rows.
    path = tmp_path / "chain.csv"
    keys = [f"C{i}" for i in range(5000)]
    hist = pd.DataFrame({"snapshot_date": "2026-07-24", "k": keys, "mid": 0.0})
    stale = pd.DataFrame({"snapshot_date": "2026-10-06", "k": keys, "mid": 1.0})
    pd.concat([hist, stale]).to_csv(path, index=False)

    data_store.upsert_csv(stale.assign(mid=2.0), path, date_col="snapshot_date",
                          key_cols=["snapshot_date", "k"])

    df = pd.read_csv(path)
    snap = df[df["snapshot_date"] == "2026-10-06"]
    assert len(snap) == 5000
    assert (snap["mid"] == 2.0).all()
    assert (df[df["snapshot_date"] == "2026-07-24"]["mid"] == 0.0).all()
