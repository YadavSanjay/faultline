"""Fixtures for the runner-output-root spec tests (docs/specs/runner-output-root.md).

The committed market-data fixture lives in ``fixture_marketdata/`` (a folder named ``data`` would be
gitignored). It is synthetic: daily bars for NVDA and TSM from 2026-09-28 to ``VDATE`` (2026-10-05, a
Monday) in ``equity/prices_daily.csv``, and four synthetic dividends in ``equity/dividends.csv`` (both
the columns the cash-equity adapter and the schema gate need). That is enough for the two cash-equity
trades of the default book, ``TRADE_1`` (NVDA) and ``TRADE_2`` (TSM), to price ``ok`` on ``VDATE``.
Every other declared pricer input is absent, so other trades fail or block per trade (the engine
isolates them), which none of these tests depend on.

``make_input`` copies the fixture into the ``marketdata`` folder of a fresh input root's data base
(``storage.data_base(input_root)``, i.e. its ``data`` folder in local mode); ``make_output``
returns a fresh, empty directory. ``tree`` records every file (with its bytes) and every directory
under a root, so "nothing created, modified or deleted" is an equality of two ``tree`` results.

Both helpers use ``tmp_path_factory`` (session scope) so hypothesis tests can call them once per
example. The module-scoped autouse fixture removes the environment variables that would move the data
root or change the book (``PRICER_DATA_URI``, ``PRICER_DEPLOY_MODE``, ``PRICER_BOOK``).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

VDATE = date(2026, 10, 5)
FIXTURE = Path(__file__).resolve().parent / "fixture_marketdata"
FIXTURE_TRADES = ("TRADE_1", "TRADE_2")   # NVDA, TSM: the trades the fixture prices ok
ASSET_CLASS = "equity"                    # both are cash-equity trades of asset class "equity"


@pytest.fixture(scope="module", autouse=True)
def _local_mode() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as mp:
        for var in ("PRICER_DATA_URI", "PRICER_DEPLOY_MODE", "PRICER_BOOK"):
            mp.delenv(var, raising=False)
        yield


def tree(root: Path) -> dict[str, bytes | None]:
    """Relative posix path -> file bytes (None for a directory), for everything under ``root``."""
    out: dict[str, bytes | None] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = None if p.is_dir() else p.read_bytes()
    return out


def files_under(root: Path) -> list[str]:
    """Relative posix paths of the files (not directories) under ``root``."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.fixture(scope="session")
def h(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    def make_input() -> Path:
        root = tmp_path_factory.mktemp("input_root")
        shutil.copytree(FIXTURE, root / "data" / "marketdata")
        return root

    def make_output() -> Path:
        return tmp_path_factory.mktemp("output_root")

    def pricer_dir(output_root: Path) -> Path:
        return output_root / "data" / "pricer"

    return SimpleNamespace(
        vdate=VDATE,
        trades=FIXTURE_TRADES,
        asset_class=ASSET_CLASS,
        make_input=make_input,
        make_output=make_output,
        pricer_dir=pricer_dir,
        tree=tree,
        files_under=files_under,
    )
