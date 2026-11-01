"""scenariogen — correlated stress-scenario generation for the AI-themed book.

Implements the design in ``docs/design/AITrade_stresstest_scenario_def_design.md``:

  S0  covariance.py    estimate Σ of factor moves (full / EWMA / crisis windows)
  S1  factor_keys.py   canonical factor taxonomy (shared units)
      factor_vector.py MaterializedScenario: a per-factor vector -> bump transforms
      scenario_gen.py  ScenarioDefinition + ScenarioGenerator (conditional-covariance)
  S2  catalog.py       ScenarioCatalog: named scenarios x severity tiers
      stress.py        ScenarioStressEngine: full-reval P&L + ES-under-shock capital
  S3  versioning.py    compute_scenario_version + ScenarioStore (governed, versioned)
      plausibility.py  PlausibilityChecker: bound + historical-envelope GATE checks
  S4  macro/           MacroRAG driver sourcing (client + handoff + GATE + provider)
  S5  reverse.py       ReverseStressSolver: solve severity that breaches a loss limit
      dashboard.py     StressDashboard + ScenarioResultStore (queryable summary)
      signoff.py       SignoffStore: analyst approve/reject gate

Scenarios stay a market-data perturbation + full revaluation on the existing P2 engine.

**Re-exports are lazy (PEP 562).** ``stress.py`` pulls in ``src.pricer.engine`` -> QuantLib, so
importing them eagerly here would make *any* ``from src.scenariogen.<sub> import ...`` — e.g. the
BFF's ``from src.scenariogen.config import load_news_config`` — execute the whole pricing stack.
The BFF image ships **without** the QuantLib wheel by design (`infra/bff/Dockerfile`), so that
eager re-export broke it with ``ModuleNotFoundError: No module named 'QuantLib'``. Resolving each
name on first attribute access keeps ``from src.scenariogen import ScenarioCatalog`` working while
leaving the package ``__init__`` free of heavy imports. Keep it that way: add new re-exports to
``_LAZY_ATTRS``, never as a module-level ``from .x import y``.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # resolved statically by mypy; never executed at runtime
    from .catalog import ScenarioCatalog
    from .covariance import FactorCovariance, build_covariance, covariance_from_history
    from .dashboard import ScenarioResultStore, StressDashboard
    from .factor_vector import MaterializedScenario, vector_to_transforms
    from .plausibility import PlausibilityChecker, PlausibilityResult
    from .reverse import ReverseStressResult, ReverseStressSolver
    from .scenario_gen import ScenarioDefinition, ScenarioGenerator
    from .signoff import SignoffRecord, SignoffStore
    from .stress import ScenarioCapital, ScenarioStressEngine, ScenarioStressResult
    from .versioning import ScenarioStore, compute_scenario_version

# exported name -> submodule it lives in
_LAZY_ATTRS: dict[str, str] = {
    "FactorCovariance": "covariance",
    "MaterializedScenario": "factor_vector",
    "PlausibilityChecker": "plausibility",
    "PlausibilityResult": "plausibility",
    "ReverseStressResult": "reverse",
    "ReverseStressSolver": "reverse",
    "ScenarioCapital": "stress",
    "ScenarioCatalog": "catalog",
    "ScenarioDefinition": "scenario_gen",
    "ScenarioGenerator": "scenario_gen",
    "ScenarioResultStore": "dashboard",
    "ScenarioStore": "versioning",
    "ScenarioStressEngine": "stress",
    "ScenarioStressResult": "stress",
    "SignoffRecord": "signoff",
    "SignoffStore": "signoff",
    "StressDashboard": "dashboard",
    "build_covariance": "covariance",
    "compute_scenario_version": "versioning",
    "covariance_from_history": "covariance",
    "vector_to_transforms": "factor_vector",
}


def __getattr__(name: str) -> Any:
    """Import the owning submodule on first access, then cache the name in the package globals."""
    submodule = _LAZY_ATTRS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{submodule}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "FactorCovariance",
    "MaterializedScenario",
    "PlausibilityChecker",
    "PlausibilityResult",
    "ReverseStressResult",
    "ReverseStressSolver",
    "ScenarioCapital",
    "ScenarioCatalog",
    "ScenarioDefinition",
    "ScenarioGenerator",
    "ScenarioResultStore",
    "ScenarioStore",
    "ScenarioStressEngine",
    "ScenarioStressResult",
    "SignoffRecord",
    "SignoffStore",
    "StressDashboard",
    "build_covariance",
    "compute_scenario_version",
    "covariance_from_history",
    "vector_to_transforms",
]
