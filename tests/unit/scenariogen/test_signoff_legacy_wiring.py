"""signoff-legacy-seq wiring: a store holding a record written before ``seq`` existed stays usable."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
from src.scenariogen.signoff import SignoffStore

LEGACY = {"scenario_version": "v_old", "scenario": "ai_bust", "status": "approved", "approver": "tester",
          "recorded_at": "2026-06-21T03:48:57.044950+00:00", "note": "S5 smoke"}


def _store_with_legacy(tmp_path) -> SignoffStore:
    store = SignoffStore(tmp_path)
    d = tmp_path / "data" / "pricer" / "store" / "signoff"
    d.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([LEGACY]), d / "v_old-20260621034857046949-0.parquet")  # no seq column
    return store


def test_legacy_record_is_listed_and_kept_in_history(tmp_path):
    store = _store_with_legacy(tmp_path)
    hist = store.history()
    assert len(hist) == 1 and hist.loc[0, "seq"] == -1
    rec = store.latest("v_old")
    assert rec is not None and rec.status == "approved" and rec.seq == -1
    assert store.is_approved("v_old")


def test_new_decision_numbers_from_zero_and_wins_over_the_legacy_one(tmp_path):
    store = _store_with_legacy(tmp_path)
    first = store.record("v_old", "ai_bust", "rejected", "risk", note="retired")
    assert first.seq == 0
    assert not store.is_approved("v_old")
    assert store.latest("v_old").note == "retired"
    second = store.record("v_new", "ai_deflation", "approved", "risk")
    assert second.seq == 1
    hist = store.history()
    assert list(hist["seq"]) == [1, 0, -1]                      # newest first, legacy last
    assert len(store.history("v_old")) == 2                       # the legacy record is still there
