"""Synthetic authoring documents and hand oracles for the issuer-scenario-support spec tests.

Everything is hand-built (docs/specs/issuer-scenario-support.md, Notes): minimal valid authoring
scenarios (snake_case name, non-empty narrative, one driver / overlay / replay window, no required
services) with a chosen factor key placed in one of the three places ``authoring.validate`` reads
factor keys from. ``errors_of`` runs ``validate`` and returns the error list (empty when it accepts), so
a wrong acceptance or rejection fails as an assertion. ``hand_source`` is Requirement 2 written out by
hand; it never calls the code under test.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest
from src.scenariogen.authoring import AuthoringError, validate

PLACES = ("driver", "overlay", "replay")
VALID_SOURCES = ("crisis", "production")


def scenario_with(key: str, place: str) -> dict[str, Any]:
    """A minimal valid authoring scenario carrying ``key`` as a driver, an overlay or a replay label."""
    if place == "driver":
        return {"narrative": "issuer widening", "required_services": [],
                "drivers": {key: {"move_bp": 150}}}
    if place == "overlay":
        return {"narrative": "issuer widening", "required_services": [],
                "drivers": {"eq:XLK": {"move_pct": -5}}, "overlays": {key: {"move_bp": 150}}}
    if place == "replay":
        return {"narrative": "issuer widening", "required_services": [],
                "replay": {"start": "2025-09-01", "end": "2025-12-31",
                           "idiosyncratic": {key: "issuer-specific move"}}}
    raise ValueError(place)


def doc_of(**scenarios: dict[str, Any]) -> dict[str, Any]:
    """An authoring document holding the given named scenarios."""
    return {"description": "issuer-scenario-support spec", "scenarios": dict(scenarios)}


def errors_of(doc: dict[str, Any]) -> list[str]:
    """The validation errors of ``doc``: [] when ``validate`` accepts it, else ``AuthoringError.errors``."""
    try:
        validate(doc)
    except AuthoringError as exc:
        return list(exc.errors)
    return []


def rejection_message(name: str, key: str, place: str) -> str:
    """The message authoring gives today for a rejected key (unchanged by Requirement 1)."""
    if place == "replay":
        return f"{name}: invalid idiosyncratic factor key {key!r}"
    return f"{name}: invalid factor key {key!r} (see factor_keys.py)"


_BAD = object()


def hand_source(spec: Mapping[str, Any]) -> object:
    """Requirement 2 by hand: the severe-tier covariance source, or ``_BAD`` when it must raise."""
    sev = spec.get("severity")
    if not isinstance(sev, Mapping) or "severe_sigma" not in sev:
        return "crisis"
    value = sev["severe_sigma"]
    if isinstance(value, str) and value in VALID_SOURCES:
        return value
    return _BAD


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        PLACES=PLACES, VALID_SOURCES=VALID_SOURCES, BAD=_BAD, scenario_with=scenario_with, doc_of=doc_of,
        errors_of=errors_of, rejection_message=rejection_message, hand_source=hand_source,
    )
