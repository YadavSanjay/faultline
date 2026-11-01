"""Subsystem import boundaries.

The pricer and marketdata meet only through data files and ``src.common``: the pricer never imports
``src.marketdata`` (a rule both need, such as which SDR records are prices, lives in
``src.common``), and ``src.common`` imports no subsystem, so it can't become a back door.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"


def _imports(package: str) -> list[tuple[str, int, str]]:
    """(file, line, imported module) for every absolute ``src.*`` import under ``src/<package>``."""
    found = []
    for path in sorted((_SRC / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.append((path.relative_to(_SRC).as_posix(), node.lineno, node.module))
            elif isinstance(node, ast.Import):
                found += [(path.relative_to(_SRC).as_posix(), node.lineno, a.name) for a in node.names]
    return found


@pytest.mark.parametrize(("package", "forbidden"), [
    ("pricer", ("src.marketdata",)),
    ("common", ("src.marketdata", "src.pricer", "src.scenariogen")),
])
def test_package_does_not_import(package, forbidden):
    bad = [f"{f}:{line} imports {mod}" for f, line, mod in _imports(package)
           if any(mod == p or mod.startswith(p + ".") for p in forbidden)]
    assert bad == [], "boundary crossed (share via src/common instead):\n" + "\n".join(bad)


def test_scanner_sees_imports():
    # Guards the guard: the pricer does import src.common, so an empty scan would be a scanner bug.
    assert any(mod.startswith("src.common") for _, _, mod in _imports("pricer"))
