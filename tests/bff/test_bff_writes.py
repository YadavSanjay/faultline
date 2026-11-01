"""BFF gated-write layer: token auth, audit trail, and scenario sign-off writes."""

from __future__ import annotations

from pathlib import Path

import pytest
from bff_helpers import write_active_portfolio, write_book_variant, write_roles_config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from ui.bff.app import audit, scenarios, security
from ui.bff.app.main import app


# --- auth gate ------------------------------------------------------------- #
def test_require_actor_local_trust_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QLP_WRITE_TOKEN", raising=False)
    assert security.write_auth_mode() == "local-trust"
    assert security.require_actor(x_qlp_token=None, x_qlp_actor="alice") == "alice"
    assert security.require_actor(x_qlp_token=None, x_qlp_actor=None) == "local"


def test_require_actor_enforces_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QLP_WRITE_TOKEN", "s3cret")
    assert security.write_auth_mode() == "token"
    with pytest.raises(HTTPException) as ei:
        security.require_actor(x_qlp_token="wrong", x_qlp_actor="alice")
    assert ei.value.status_code == 401
    # correct token passes and returns the actor
    assert security.require_actor(x_qlp_token="s3cret", x_qlp_actor="alice") == "alice"


# --- audit trail ----------------------------------------------------------- #
def test_audit_records_and_reads_newest_first(bff_root: Path) -> None:
    audit.record_action("scenario.signoff", "VER1", "alice", reason="looks good",
                        details={"status": "approved"})
    audit.record_action("scenario.signoff", "VER2", "bob", outcome="error")

    entries = audit.recent(10)
    assert len(entries) == 2
    assert entries[0]["target"] == "VER2"      # newest first
    assert entries[0]["outcome"] == "error"
    assert entries[1]["actor"] == "alice"
    assert entries[1]["details"]["status"] == "approved"


# --- sign-off write -------------------------------------------------------- #
def test_record_signoff_writes_and_reads_back(bff_root: Path) -> None:
    out = scenarios.record_signoff("VER1", "taiwan", "approved", "alice", "looks good")
    assert out["ok"] is True
    assert out["record"]["status"] == "approved"

    recs = scenarios.signoff()["records"]
    assert recs["VER1"]["status"] == "approved"
    assert recs["VER1"]["approver"] == "alice"
    assert recs["VER1"]["note"] == "looks good"


def test_record_signoff_latest_wins(bff_root: Path) -> None:
    scenarios.record_signoff("VER1", "taiwan", "approved", "alice")
    scenarios.record_signoff("VER1", "taiwan", "rejected", "bob", "changed my mind")

    recs = scenarios.signoff()["records"]
    assert recs["VER1"]["status"] == "rejected"
    assert recs["VER1"]["approver"] == "bob"


def test_record_signoff_rejects_bad_status(bff_root: Path) -> None:
    out = scenarios.record_signoff("VER1", "taiwan", "maybe", "alice")
    assert out["ok"] is False
    assert "approved" in out["error"]


# --- POST route wiring (Depends + audit) via TestClient -------------------- #
def test_post_activate_route_writes_and_audits(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QLP_WRITE_TOKEN", raising=False)
    write_active_portfolio(bff_root, {"name": "BASE", "trades": [{"trade_id": "T1"}]})
    write_book_variant(bff_root, "aggressive", {
        "name": "Aggressive", "sizing_basis": {"profile": "aggressive"},
        "trades": [{"trade_id": "T1"}, {"trade_id": "T2"}],
    })
    client = TestClient(app)

    resp = client.post("/api/sizing/activate", json={"profile": "aggressive", "note": "go aggressive"},
                       headers={"X-QLP-Actor": "alice"})
    assert resp.status_code == 200
    assert resp.json()["profile"] == "aggressive"

    # GET /sizing/active resolves to the literal route (not /sizing/{profile}) and reflects the write
    assert client.get("/api/sizing/active").json()["profile"] == "aggressive"

    # the write is audited under the actor
    entries = client.get("/api/audit").json()["entries"]
    assert entries[0]["action"] == "sizing.activate_variant"
    assert entries[0]["actor"] == "alice"

    # an unknown variant is a 400 (and still audited as an error)
    bad = client.post("/api/sizing/activate", json={"profile": "nope"}, headers={"X-QLP-Actor": "alice"})
    assert bad.status_code == 400


def test_post_signoff_requires_token_when_configured(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QLP_WRITE_TOKEN", "s3cret")
    client = TestClient(app)
    # missing token -> 401
    resp = client.post("/api/scenarios/signoff", json={"scenario_version": "V1", "status": "approved"})
    assert resp.status_code == 401
    # correct token -> allowed
    ok = client.post("/api/scenarios/signoff", json={"scenario_version": "V1", "scenario": "s", "status": "approved"},
                     headers={"X-QLP-Token": "s3cret", "X-QLP-Actor": "alice"})
    assert ok.status_code == 200


# --- per-operator RBAC ----------------------------------------------------- #
def test_rbac_role_lookup_and_hierarchy(bff_root: Path) -> None:
    write_roles_config(bff_root, {
        "default_role": "viewer",
        "roles": {"alice": "admin", "bob": "approver"},
        "action_roles": {"scenario.signoff": "approver"},
    })
    assert security.role_for("alice") == "admin"
    assert security.role_for("bob") == "approver"
    assert security.role_for("nobody") == "viewer"      # default
    assert security.required_role("scenario.signoff") == "approver"
    assert security._satisfies("admin", "approver") is True
    assert security._satisfies("viewer", "approver") is False


def test_rbac_blocks_insufficient_role(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QLP_WRITE_TOKEN", raising=False)
    write_roles_config(bff_root, {
        "default_role": "viewer",
        "roles": {"desk": "approver"},
        "action_roles": {"scenario.signoff": "approver"},
    })
    client = TestClient(app)
    body = {"scenario_version": "V1", "scenario": "s", "status": "approved"}

    # a default 'viewer' cannot sign off -> 403
    blocked = client.post("/api/scenarios/signoff", json=body, headers={"X-QLP-Actor": "randomguy"})
    assert blocked.status_code == 403

    # an 'approver' can -> 200
    allowed = client.post("/api/scenarios/signoff", json=body, headers={"X-QLP-Actor": "desk"})
    assert allowed.status_code == 200
