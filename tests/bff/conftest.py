"""Fixtures for the BFF read-layer tests.

The BFF resolves the platform's data/config tree from ``QLP_ROOT`` (see ui/bff/app/settings.py)
and memoises several lookups with ``lru_cache``. The ``bff_root`` fixture points the BFF at a
throwaway temp root and clears those caches before *and* after each test, so a test builds
exactly the stored artifacts it needs and never sees (or pollutes) the real repo data.

Artifact builders live in ``bff_helpers.py`` (a uniquely-named module) so the tests can
``from bff_helpers import ...`` without depending on conftest collection order.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ui.bff.app import marketdata, pricing, scenarios, settings

# every lru_cache in the BFF read layer that captures the root or a config/file
_CACHED = (
    settings.project_root,
    pricing._portfolio_index,
    marketdata._source_index,
    marketdata._dq_config,
    marketdata._snapshot_index,
    marketdata._csv_stats_cached,
    marketdata._trades_by_asset,
    scenarios._scenario_config,
    scenarios._signoff_by_version,
)


def _clear_caches() -> None:
    for fn in _CACHED:
        fn.cache_clear()


@pytest.fixture
def bff_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QLP_ROOT", str(tmp_path))
    _clear_caches()
    (tmp_path / "data").mkdir()
    yield tmp_path
    _clear_caches()
