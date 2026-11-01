"""Path bootstrap for scenariogen tests (import src.* from the project root)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from market_data_marker import skip_without_market_data


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    skip_without_market_data(items)
