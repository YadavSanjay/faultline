"""Write-gate for the BFF (gated-writes, local token model).

V1 of the platform is read-only. Writes (starting with scenario sign-off) go through a single
gate: if ``QLP_WRITE_TOKEN`` is configured, every write must present a matching ``X-QLP-Token``
header; otherwise the BFF runs in local-trust mode (writes allowed without a token). Either way
the *actor* (``X-QLP-Actor`` header, default ``local``) is captured for the append-only audit
trail — every mutation is attributable. This is the pluggable token/header hook BUILD_PLAN
(ENG-5) calls for before any non-local deploy; per-user RBAC can layer on top later.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from fastapi import Header, HTTPException

from .settings import project_root

# role hierarchy: a higher level satisfies any lower requirement
ROLE_LEVEL = {"viewer": 0, "approver": 1, "operator": 2, "admin": 3}


def _configured_token() -> str | None:
    tok = os.environ.get("QLP_WRITE_TOKEN")
    return tok or None


def write_auth_mode() -> str:
    return "token" if _configured_token() is not None else "local-trust"


def require_actor(
    x_qlp_token: str | None = Header(default=None),
    x_qlp_actor: str | None = Header(default=None),
) -> str:
    """Enforce the shared token when one is configured; return the actor for the audit record."""
    token = _configured_token()
    if token is not None and x_qlp_token != token:
        raise HTTPException(status_code=401, detail="invalid or missing write token")
    actor = (x_qlp_actor or "").strip() or "local"
    return actor[:120]


# --- per-operator RBAC ----------------------------------------------------- #
def roles_config() -> dict[str, Any]:
    """Operator→role map + per-action required role (configs/ui/roles.json)."""
    path = project_root() / "configs" / "ui" / "roles.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        cfg = {}
    return {
        "default_role": cfg.get("default_role", "operator"),
        "roles": cfg.get("roles", {}) or {},
        "action_roles": cfg.get("action_roles", {}) or {},
    }


def role_for(actor: str) -> str:
    cfg = roles_config()
    return cfg["roles"].get(actor) or cfg["default_role"]


def required_role(action: str) -> str:
    return roles_config()["action_roles"].get(action, "operator")


def _satisfies(actor_role: str, needed: str) -> bool:
    return ROLE_LEVEL.get(actor_role, -1) >= ROLE_LEVEL.get(needed, 99)


def require_write(action: str) -> Callable[..., str]:
    """Dependency factory: token gate + actor + RBAC. The actor's role (from roles.json) must
    satisfy the action's required role, else 403. Under local-trust the actor is self-declared,
    so this enforces role *discipline* + audit; it becomes an identity boundary once a write
    token / real auth authenticates the actor."""

    def dep(
        x_qlp_token: str | None = Header(default=None),
        x_qlp_actor: str | None = Header(default=None),
    ) -> str:
        actor = require_actor(x_qlp_token, x_qlp_actor)
        actor_role = role_for(actor)
        needed = required_role(action)
        if not _satisfies(actor_role, needed):
            raise HTTPException(
                status_code=403,
                detail=f"role '{actor_role}' cannot perform '{action}' (requires '{needed}')",
            )
        return actor

    return dep
