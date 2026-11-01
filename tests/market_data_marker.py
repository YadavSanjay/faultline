"""Skip the tests that need ingested market data on a checkout that has none.

``data/`` is gitignored and its feeds can't be redistributed, so a fresh clone starts without them.
Tests that read them carry ``@pytest.mark.needs_market_data`` and skip with a pointer to the ingest
guide, rather than failing on a missing file, until the core feeds are all in. A checkout with those
but without a smaller feed still runs them, and the tests on that feed fail. Where the data must be there, set
``FAULTLINE_REQUIRE_DATA=1``: the marked tests then run and fail loud, so a lost feed can't turn into a
quiet skip.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import pytest
from src.common import storage

MARKER = "needs_market_data"
REQUIRE_ENV = "FAULTLINE_REQUIRE_DATA"
SKIP_REASON = "needs ingested market data: see docs/guides/marketdata_guide.md"
CORE_FILES = (
    "rates/pensford_sofr_forward.csv",
    "equity/prices_daily.csv",
    "bonds/treasury_par_yields.csv",
)
OPTION_CHAINS = "options/chains"


def has_market_data() -> bool:
    """Whether the configured data tree holds every core feed.

    One ingested feed creates ``marketdata/``, so the folder alone says nothing about the rest.
    """
    base = storage.join(storage.data_base(), "marketdata")
    return all(storage.exists(storage.join(base, rel)) for rel in CORE_FILES) and bool(
        storage.glob(storage.join(base, OPTION_CHAINS), "*.csv"))


def skip_without_market_data(items: Iterable[pytest.Item]) -> None:
    """Mark the ``needs_market_data`` tests in ``items`` as skipped when there is no market data.

    Args:
        items: The collected tests, as passed to ``pytest_collection_modifyitems``.
    """
    if os.environ.get(REQUIRE_ENV) == "1" or has_market_data():
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if item.get_closest_marker(MARKER) is not None:
            item.add_marker(skip)
