"""P1 curve-cache tests: a distinct curve is bootstrapped once per run."""

from __future__ import annotations

from src.pricer.ql.common.curve_builder import build_ois_curve
from src.pricer.ql.common.curve_cache import CurveCache, cached, curve_cache
from src.pricer.runner import _even_chunks


def test_no_cache_builds_fresh_each_call(curve_data):
    # Outside a curve_cache() block every call bootstraps a new handle.
    a = build_ois_curve(curve_data)
    b = build_ois_curve(curve_data)
    assert a is not b


def test_cache_reuses_identical_curve(curve_data):
    with curve_cache() as cc:
        a = build_ois_curve(curve_data)
        b = build_ois_curve(curve_data)
    assert a is b                      # same handle reused
    assert cc.hits == 1 and cc.misses == 1
    assert len(cc.store) == 1          # one distinct curve built


def test_cache_separates_by_interpolation(curve_data):
    with curve_cache() as cc:
        ff = build_ois_curve(curve_data, interpolation="flatforward")
        lc = build_ois_curve(curve_data, interpolation="logcubic")
        ff2 = build_ois_curve(curve_data, interpolation="flatforward")
    assert ff is ff2 and ff is not lc
    assert len(cc.store) == 2
    assert cc.misses == 2 and cc.hits == 1


def test_cached_helper_no_active_cache_just_builds():
    calls = []
    out = cached("k", lambda: calls.append(1) or "v")
    assert out == "v" and calls == [1]


def test_cache_object_counts():
    cc = CurveCache()
    assert cc.get_or_build("x", lambda: 1) == 1
    assert cc.get_or_build("x", lambda: 2) == 1   # cached, builder not run
    assert cc.hits == 1 and cc.misses == 1


def test_even_chunks():
    assert _even_chunks(["a", "b", "c", "d", "e"], 2) == [["a", "b", "c"], ["d", "e"]]
    assert _even_chunks(["a"], 4) == [["a"]]               # never more chunks than items
    assert _even_chunks(["a", "b", "c"], 3) == [["a"], ["b"], ["c"]]
