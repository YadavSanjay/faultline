"""signoff-replay-versions wiring: the CLI's default version is the catalog's resolved (registered) version."""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from src.scenariogen.catalog import REPLAY_UNCOVERED, ScenarioCatalog, resolve_replay
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.signoff_runner import _current_version
from src.scenariogen.versioning import compute_scenario_version

ROOT = Path(__file__).resolve().parents[3]
DAYS = ["2025-01-21", "2025-01-22", "2025-01-23", "2025-01-24", "2025-01-27"]
SERIES = {"eq:XLK": pd.Series([0.0, 0.5, 0.25, -0.5, 0.125], index=DAYS)}
COV = FactorCovariance(factors=("eq:XLK",), mean=np.zeros(1), cov=np.array([[0.0004]]), n_obs=250,
                       window="test", cov_id="toy")


def _catalog() -> ScenarioCatalog:
    raw = {"replay": {"start": "2025-01-21", "end": "2025-01-27"}, "overlays": {}}
    resolved, info = resolve_replay("rp", raw, SERIES, COV.factors)
    authored = {"drivers": {"eq:XLK": {"move_pct": -10}}, "method": "conditional_covariance", "horizon_days": 10}
    return ScenarioCatalog(covariance=COV, specs={"rp": resolved, "auth": authored, "gone": {"replay": {}}},
                           severity_tiers={"mild": 1.0}, sensitivities={"sens": authored}, replays={"rp": info},
                           errors={"gone": ScenarioGenerationError("gone", REPLAY_UNCOVERED, "outside history")})


def test_default_version_is_the_resolved_one():
    cat = _catalog()
    assert _current_version("rp", cat) == compute_scenario_version(cat.spec("rp"))
    assert _current_version("rp", cat) != compute_scenario_version({"replay": {}, "overlays": {}})
    assert _current_version("auth", cat) == compute_scenario_version(cat.specs["auth"])
    assert _current_version("sens", cat) == compute_scenario_version(cat.sensitivities["sens"])


def test_unresolvable_and_unknown_names_exit_with_a_reason():
    cat = _catalog()
    with pytest.raises(SystemExit, match="can't be resolved"):
        _current_version("gone", cat)
    with pytest.raises(SystemExit, match="unknown scenario"):
        _current_version("nope", cat)


def _runtime_imports(path: Path) -> set[str]:
    """Modules a file imports at run time (module level, outside if TYPE_CHECKING:), relative ones resolved."""
    out: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(f"src.scenariogen.{node.module}" if node.level else str(node.module))
    return out


def test_signoff_module_imports_no_pricing_code_at_run_time():
    mods = _runtime_imports(ROOT / "src" / "scenariogen" / "signoff.py")
    assert not {m for m in mods if m.startswith(("src.pricer", "src.scenariogen.catalog"))}, mods
    for dep in ("errors", "versioning"):           # its own scenariogen imports stay light too
        sub = _runtime_imports(ROOT / "src" / "scenariogen" / f"{dep}.py")
        assert not {m for m in sub if m.startswith(("src.pricer", "src.scenariogen"))}, (dep, sub)
