"""Environment-variable expansion for config values (harness-audit F6).

Expands ``${VAR}`` and ``${VAR:-default}`` placeholders in loaded-config strings so a service's
LOCATION — host repo path, base URL, inference URL — can be overridden from the environment in
**one place** instead of edited across several JSON files. An unset var *with no default* is
left verbatim (so a typo stays visible rather than silently blanking a path). Substitution is
partial: ``${ARAG_HOME:-/opt/arag}/src`` expands the placeholder and keeps the ``/src`` suffix.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_str(value: str, environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ

    def _sub(m: re.Match[str]) -> str:
        var, default = m.group(1), m.group(2)
        if var in env:
            return env[var]
        return default if default is not None else m.group(0)

    return _PLACEHOLDER.sub(_sub, value)


def expand_env_vars(obj: Any, environ: Mapping[str, str] | None = None) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in every string of a JSON-like object."""
    if isinstance(obj, str):
        return expand_str(obj, environ)
    if isinstance(obj, dict):
        return {k: expand_env_vars(v, environ) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env_vars(v, environ) for v in obj]
    return obj
