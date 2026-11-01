"""Contract: the BFF app imports with **no QuantLib installed**.

``infra/bff/Dockerfile`` deliberately strips the QuantLib wheel — the BFF is the compute-free
image and offloads pricing to the backend via the JobRunner seam. That makes "every ``ui.bff.app``
import resolves without QuantLib" a build contract, not a nicety: a single eager re-export in a
package ``__init__`` (``src/scenariogen/__init__.py`` did exactly this) crashes the container at
startup with ``ModuleNotFoundError: No module named 'QuantLib'``.

The check runs in a **subprocess** on purpose: the pytest process has QuantLib imported by the
pricer tests, so only a fresh interpreter can faithfully simulate the image.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# Makes `import QuantLib` fail the way its absence from the image does.
_PRELUDE = """
import sys

class _BlockQuantLib:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "QuantLib" or fullname.startswith("QuantLib."):
            raise ModuleNotFoundError("No module named %r" % fullname, name=fullname)
        return None

sys.meta_path.insert(0, _BlockQuantLib())
"""

_EPILOGUE = "\nassert 'QuantLib' not in sys.modules, 'QuantLib was imported'\n"


def _run(body: str) -> subprocess.CompletedProcess[str]:
    """Run `body` in a fresh interpreter that has no QuantLib, from the repo root."""
    return subprocess.run(
        [sys.executable, "-c", _PRELUDE + body + _EPILOGUE],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


# The BFF also defers some `src.*` imports into request handlers (ui/bff/app/scenarios.py,
# system.py, jobs.py), so import-time cleanliness alone doesn't prove the container serves — a
# deferred import would only blow up on the first request that reaches it.
_DEFERRED_IMPORTS = [
    ("src.common.jobs", "JobRecordStore"),
    ("src.common.jobs", "get_job_runner"),
    ("src.pricer.output.result_store", "ResultStore"),
    ("src.pricer.config", "load_pricer_config"),
    ("src.pricer.risk.factor_history", "FactorHistory"),
    ("src.pricer.settings", "get_settings"),
    ("src.scenariogen.config", "load_covariance_config"),
    ("src.scenariogen.config", "load_news_config"),
    ("src.scenariogen.covariance", "covariance_from_history"),
    ("src.scenariogen.news.gate", "NewsGateConfig"),
    ("src.scenariogen.news.mapping", "NewsMapConfig"),
    ("src.scenariogen.news.provider", "NewsEventProvider"),
    ("src.scenariogen.signoff", "SignoffStore"),
]


def test_bff_app_imports_without_quantlib() -> None:
    """The exact module the container's uvicorn CMD loads: `ui.bff.app.main:app`."""
    proc = _run("import ui.bff.app.main")
    assert proc.returncode == 0, f"BFF import pulled in QuantLib:\n{proc.stderr}"


def test_bff_deferred_request_time_imports_are_quantlib_free() -> None:
    body = "\n".join(f"from {mod} import {attr}" for mod, attr in _DEFERRED_IMPORTS)
    proc = _run(body)
    assert proc.returncode == 0, f"a request-time BFF import needs QuantLib:\n{proc.stderr}"


def test_scenariogen_package_init_stays_import_light() -> None:
    """``src.scenariogen`` re-exports lazily (PEP 562), so importing the package must not price.

    Only the bare package import is checked here — resolving a name like ``ScenarioStressEngine``
    *should* pull QuantLib in, since that is what it wraps. Resolution is covered below, in the
    normal interpreter.
    """
    proc = _run("import src.scenariogen")
    assert proc.returncode == 0, f"scenariogen/__init__ eagerly imported QuantLib:\n{proc.stderr}"


def test_scenariogen_lazy_exports_all_resolve() -> None:
    """Lazily or not, every advertised name must still resolve — `__all__` is the public API."""
    import src.scenariogen as sg

    for name in sg.__all__:
        assert getattr(sg, name) is not None, f"{name} in __all__ but not resolvable"

    assert sorted(sg._LAZY_ATTRS) == sorted(sg.__all__), "_LAZY_ATTRS and __all__ drifted"

    missing = "NotAnExport"                              # __getattr__ must not swallow typos
    with pytest.raises(AttributeError, match=missing):
        getattr(sg, missing)
