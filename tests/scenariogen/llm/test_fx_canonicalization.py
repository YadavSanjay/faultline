"""FX factor canonicalization: MacroRAG USD-pair names -> canonical fx:<CCY> (offline)."""

from __future__ import annotations

import pytest
from src.scenariogen.factor_keys import canonical_fx_key
from src.scenariogen.llm.phases.calibrate import _canonicalize_factors
from src.scenariogen.llm.schema import CalibratedScenario, Citation, DriverLeg


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("fx:USDTWD", "fx:TWD"),   # MacroRAG USD-base pair -> foreign leg
        ("fx:USDJPY", "fx:JPY"),
        ("fx:EURUSD", "fx:EUR"),   # trailing-USD pair collapses too
        ("fx:TWD", "fx:TWD"),      # already canonical
        ("fx:twd", "fx:TWD"),      # upper-cased
        ("fx:usdtwd", "fx:TWD"),
        ("eq:NVDA", "eq:NVDA"),    # non-fx keys untouched
        ("ir:DGS10", "ir:DGS10"),
        ("credit", "credit"),
    ],
)
def test_canonical_fx_key(raw: str, expected: str) -> None:
    assert canonical_fx_key(raw) == expected


def test_canonicalize_factors_normalizes_legs_and_overlays() -> None:
    scenario = CalibratedScenario(
        name="s",
        legs=[
            DriverLeg(factor="fx:USDTWD", move=-0.08, origin="macro",
                      citations=[Citation(chunk_id="c1", source="macro")]),
            DriverLeg(factor="eq:NVDA", move=-0.2, origin="equity"),
        ],
        overlays={"fx:USDJPY": 0.03, "eq:TSLA": -0.05},
    )
    out = _canonicalize_factors(scenario)
    assert [leg.factor for leg in out.legs] == ["fx:TWD", "eq:NVDA"]
    # move + provenance (citations) preserved on the normalized leg
    assert out.legs[0].move == -0.08
    assert out.legs[0].citations[0].chunk_id == "c1"
    assert out.overlays == {"fx:JPY": 0.03, "eq:TSLA": -0.05}
