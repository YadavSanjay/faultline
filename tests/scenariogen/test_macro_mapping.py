"""S4 mapping tests: MacroRAG shock-vector fields -> canonical factor keys + units."""

from __future__ import annotations

import pytest
from src.scenariogen.macro.mapping import to_canonical_drivers
from src.scenariogen.macro.models import MacroShockField


def _f(name, value, unit="", chunks=("c",)):
    return MacroShockField(name=name, value=value, unit=unit, chunk_ids=list(chunks))


def _by_factor(drivers):
    return {d.factor: d for d in drivers}


def test_ir_credit_fx_vol_unit_conversions():
    d = _by_factor(to_canonical_drivers([
        _f("UST_10Y", 75, "bp"), _f("IG_OAS", 90, "bp"),
        _f("USDTWD", 8, "pct"), _f("USDJPY", 6, "pct"),
        _f("VIX", 20, "pts"), _f("MOVE", 40, "pts"),
    ], move_base=100.0))
    assert d["ir:DGS10"].move == pytest.approx(0.0075)
    assert d["credit"].move == pytest.approx(0.0090)
    assert d["fx:TWD"].move == pytest.approx(0.08)
    assert d["fx:JPY"].move == pytest.approx(0.06)
    assert d["eqvol"].move == pytest.approx(0.20)
    assert d["ratesvol"].move == pytest.approx(0.40)       # 40 pts / base 100


def test_move_rel_unit_taken_as_is():
    d = _by_factor(to_canonical_drivers([_f("MOVE", 0.35, "rel")]))
    assert d["ratesvol"].move == pytest.approx(0.35)


def test_ust_parallel_expands_to_all_nodes():
    from src.scenariogen import factor_keys as fk
    d = _by_factor(to_canonical_drivers([_f("UST", 50, "bp")]))
    assert set(d) == {f"ir:{t}" for t in fk.IR_TENOR_YEARS}    # all curve control nodes
    assert all(abs(v.move - 0.0050) < 1e-12 for v in d.values())


def test_hy_used_only_without_ig():
    only_hy = _by_factor(to_canonical_drivers([_f("HY_OAS", 200, "bp")]))
    assert only_hy["credit"].move == pytest.approx(0.02)
    both = _by_factor(to_canonical_drivers([_f("IG_OAS", 90, "bp"), _f("HY_OAS", 200, "bp")]))
    assert both["credit"].macro_field == "IG_OAS"          # IG preferred


def test_unmapped_fields_dropped_and_citations_carried():
    drivers = to_canonical_drivers([_f("XCCY_BASIS", 50, "bp"),
                                    _f("USDTWD", 8, "pct", chunks=("a", "b"))])
    by = _by_factor(drivers)
    assert "fx:TWD" in by and len(drivers) == 1            # basis dropped
    assert by["fx:TWD"].chunk_ids == ["a", "b"]
