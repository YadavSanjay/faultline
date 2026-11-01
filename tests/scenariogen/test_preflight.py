"""Dual-RAG preflight tests: fail loudly when a required service is down."""

from __future__ import annotations

import pytest
from src.scenariogen.arag.models import AragHealth
from src.scenariogen.preflight import (
    ServiceUnavailableError,
    preflight_services,
)


class FakeMacro:
    def __init__(self, status: str = "ok", boom: bool = False):
        self._status, self._boom = status, boom

    def healthz(self) -> dict:
        if self._boom:
            raise RuntimeError("connection refused")
        return {"status": self._status, "built": True}


class FakeArag:
    def __init__(self, *, status: str = "ok", pipelines: bool = True, boom: bool = False):
        self._h = AragHealth(status=status, pipelines=pipelines)
        self._boom = boom

    def health(self) -> AragHealth:
        if self._boom:
            raise RuntimeError("connection refused")
        return self._h


def test_all_required_up_returns_health_map():
    res = preflight_services(["macro", "arag"], macro_client=FakeMacro(), arag_client=FakeArag())
    assert res["macro"].ok and res["arag"].ok
    assert res["macro"].required and res["arag"].required


def test_required_service_down_raises():
    with pytest.raises(ServiceUnavailableError) as ei:
        preflight_services(["macro", "arag"], macro_client=FakeMacro(),
                           arag_client=FakeArag(boom=True))
    assert [f.name for f in ei.value.failures] == ["arag"]


def test_required_arag_not_ready_raises():
    # reachable but retrieval pipelines not importable -> not ready -> fail-loud
    with pytest.raises(ServiceUnavailableError):
        preflight_services(["arag"], arag_client=FakeArag(pipelines=False))


def test_missing_required_client_raises():
    with pytest.raises(ServiceUnavailableError) as ei:
        preflight_services(["macro", "arag"], macro_client=FakeMacro())   # no arag client wired
    assert ei.value.failures[0].name == "arag"
    assert "no client wired" in ei.value.failures[0].detail


def test_non_required_down_does_not_block():
    # arag is down but not required -> no raise; still reported for the UI
    res = preflight_services(["macro"], macro_client=FakeMacro(), arag_client=FakeArag(boom=True))
    assert res["macro"].ok
    assert not res["arag"].ok and not res["arag"].required


def test_only_probes_wired_and_required():
    res = preflight_services(["macro"], macro_client=FakeMacro())
    assert set(res) == {"macro"}       # arag neither required nor wired -> absent
