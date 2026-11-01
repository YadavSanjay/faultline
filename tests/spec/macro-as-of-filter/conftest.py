"""Hand-built MacroRAG filters and hand oracles for the macro-as-of-filter spec tests.

Everything is synthetic (docs/specs/macro-as-of-filter.md, Notes): ``full_filter`` builds a
``MacroFilter`` with every facet populated, so "every other field unchanged" is a real check, and
``expected`` is Requirement 1 written out by hand (it never calls the code under test).

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from src.scenariogen.macro.models import MacroFilter

AS_OF = date(2026, 10, 6)


def full_filter(date_from: str | None = "2025-01-01", date_to: str | None = None) -> MacroFilter:
    """A filter with every facet populated and the given date window."""
    return MacroFilter(
        tiers=[1, 2],
        buckets=["monetary_policy", "credit"],
        doc_types=["speech", "market_episode"],
        entity_tags=["FED", "ECB"],
        factor_tags=["rates", "credit_spreads"],
        publishers=["Federal Reserve", "BIS"],
        date_from=date_from,
        date_to=date_to,
    )


def expected(filters: MacroFilter, date_to: str | None) -> MacroFilter:
    """``filters`` field by field (fresh lists) with ``date_to`` replaced: the hand oracle."""
    return MacroFilter(
        tiers=list(filters.tiers),
        buckets=list(filters.buckets),
        doc_types=list(filters.doc_types),
        entity_tags=list(filters.entity_tags),
        factor_tags=list(filters.factor_tags),
        publishers=list(filters.publishers),
        date_from=filters.date_from,
        date_to=date_to,
    )


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(AS_OF=AS_OF, full_filter=full_filter, expected=expected)
