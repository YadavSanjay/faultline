"""S5 sign-off tests: append-only approve/reject log; latest decision wins."""

from __future__ import annotations

import pytest
from src.scenariogen.signoff import SignoffStore


def test_approve_then_is_approved(tmp_path):
    store = SignoffStore(tmp_path)
    store.record("v1", "taiwan", "approved", "alice", note="Q3 review")
    assert store.is_approved("v1")
    rec = store.latest("v1")
    assert rec is not None and rec.status == "approved" and rec.approver == "alice"


def test_latest_decision_wins(tmp_path):
    store = SignoffStore(tmp_path)
    store.record("v1", "taiwan", "approved", "alice")
    store.record("v1", "taiwan", "rejected", "bob", note="model concern")
    assert not store.is_approved("v1")
    assert store.latest("v1").approver == "bob"


def test_unknown_version_not_approved(tmp_path):
    assert not SignoffStore(tmp_path).is_approved("nope")
    assert SignoffStore(tmp_path).latest("nope") is None


def test_versions_independent(tmp_path):
    store = SignoffStore(tmp_path)
    store.record("v1", "taiwan", "approved", "alice")
    store.record("v2", "ai_bust", "rejected", "alice")
    assert store.is_approved("v1") and not store.is_approved("v2")


def test_invalid_status_rejected(tmp_path):
    with pytest.raises(ValueError):
        SignoffStore(tmp_path).record("v1", "taiwan", "maybe", "alice")


def test_history(tmp_path):
    store = SignoffStore(tmp_path)
    store.record("v1", "taiwan", "approved", "alice")
    store.record("v1", "taiwan", "rejected", "bob")
    assert len(store.history("v1")) == 2
    assert len(store.history()) == 2
