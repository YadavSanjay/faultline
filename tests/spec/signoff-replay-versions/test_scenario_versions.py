"""signoff-replay-versions C1: the sign-off versions are the hashes of the catalog's resolved specs.

Oracle: ``compute_scenario_version`` (an existing, unchanged function, not the code under test) applied in the
test to specs the test resolves by hand from its own synthetic series: a replay's drivers are the episode moves
over ``(start, end]`` (``eq:`` compounds, ``prod(1 + r) - 1``; other factors sum), its horizon the ``eq:XLK``
row count in the window and its method ``conditional_covariance``; an authored spec is hashed as configured.
Moves are dyadic, so the hand values (exact fractions) are exact in binary. Properties: windows with different
moves give different versions; narrative/trigger edits don't change a version; revising the history under a
window changes it, outside the window doesn't.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.catalog import ScenarioCatalog, resolve_replay
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.signoff import scenario_versions
from src.scenariogen.versioning import compute_scenario_version

DAYS = ["2025-03-03", "2025-03-04", "2025-03-05", "2025-03-06", "2025-03-07", "2025-03-10", "2025-03-11",
        "2025-03-12"]
FACTORS = ("eq:XLK", "ir:DGS10", "eqvol")
OVERLAYS = {"eqvol": {"move_pts": 5}}
SEVERITY = {"lead": "eq:XLK", "direction": "down"}

XLK = [0.0, 0.5, -0.5, 0.25, -0.25, 0.125, 0.0, -0.5]
DGS10 = [0.0, 0.03125, -0.015625, 0.0625, 0.0, -0.03125, 0.015625, 0.0]
EQVOL = [0.0, 0.125, 0.0625, -0.0625, 0.25, 0.0, -0.125, 0.0625]


def _series(xlk: list[float], dgs10: list[float], eqvol: list[float]) -> dict[str, pd.Series]:
    return {"eq:XLK": pd.Series(xlk, index=DAYS), "ir:DGS10": pd.Series(dgs10, index=DAYS),
            "eqvol": pd.Series(eqvol, index=DAYS)}


def _cov() -> FactorCovariance:
    vols = np.array([0.02, 0.001, 0.01])
    corr = np.full((3, 3), 0.5) + 0.5 * np.eye(3)
    return FactorCovariance(factors=FACTORS, mean=np.zeros(3), cov=np.outer(vols, vols) * corr,
                            n_obs=250, window="test", cov_id="toy")


def _replay(start: str, end: str, narrative: str = "replay", trigger: str = "episode") -> dict[str, Any]:
    return {"narrative": narrative, "trigger": trigger, "overlays": OVERLAYS, "severity": SEVERITY,
            "replay": {"start": start, "end": end}}


def _hand_resolved(spec: dict[str, Any], drivers: dict[str, float], horizon: int) -> dict[str, Any]:
    """The resolved spec built by hand: the authored spec plus the episode drivers, horizon and method."""
    out = dict(spec)
    out["drivers"] = {k: {"move": v} for k, v in sorted(drivers.items())}
    out["horizon_days"] = horizon
    out["method"] = "conditional_covariance"
    return out


def _hand_moves(xlk: list[float], dgs10: list[float], eqvol: list[float], i: int, j: int) -> dict[str, float]:
    """Episode moves over rows ``i+1..j`` (dates in ``(DAYS[i], DAYS[j]]``) in exact fractions."""
    prod = Fraction(1)
    for r in xlk[i + 1: j + 1]:
        prod *= 1 + Fraction(r)
    return {"eq:XLK": float(prod - 1),
            "ir:DGS10": float(sum(Fraction(r) for r in dgs10[i + 1: j + 1])),
            "eqvol": float(sum(Fraction(r) for r in eqvol[i + 1: j + 1]))}


def _uncovered(name: str, spec: dict[str, Any], series: dict[str, pd.Series]) -> ScenarioGenerationError:
    with pytest.raises(ScenarioGenerationError) as ei:
        resolve_replay(name, spec, series, FACTORS)
    return ei.value


def _catalog(specs: dict[str, dict[str, Any]], sens: dict[str, dict[str, Any]], series: dict[str, pd.Series],
             unresolvable: tuple[str, ...] = ()) -> ScenarioCatalog:
    """A catalog the way ``from_config`` builds one: replays resolved, unresolvable ones in ``errors``."""
    specs, sens = dict(specs), dict(sens)
    replays: dict[str, dict[str, Any]] = {}
    errors: dict[str, ScenarioGenerationError] = {}
    for block in (specs, sens):
        for name, spec in block.items():
            if "replay" not in spec:
                continue
            if name in unresolvable:
                errors[name] = _uncovered(name, spec, series)
            else:
                block[name], replays[name] = resolve_replay(name, spec, series, FACTORS)
    return ScenarioCatalog(covariance=_cov(), specs=specs, severity_tiers={"adverse": 1.0}, sensitivities=sens,
                           replays=replays, errors=errors)


AUTHORED = {"narrative": "authored", "trigger": "t", "method": "drivers_only", "horizon_days": 10,
            "drivers": {"eq:XLK": {"move_pct": -10}}, "overlays": {"eqvol": {"move_pts": 3}}}
SENS_AUTHORED = {"narrative": "sens", "drivers": {"eqvol": 0.01}}


@pytest.mark.criterion("C1")
def test_c1_point_resolved_versions_in_catalog_order():
    # oracle: compute_scenario_version of specs resolved by hand from XLK/DGS10/EQVOL above:
    #   rp_a (03-04, 03-07]: XLK (1-0.5)(1+0.25)(1-0.25)-1 = -0.53125; DGS10 -0.015625+0.0625+0 = 0.046875;
    #       eqvol 0.0625-0.0625+0.25 = 0.25; horizon 3
    #   rp_b (03-07, 03-12]: XLK (1+0.125)(1+0)(1-0.5)-1 = -0.4375; DGS10 -0.03125+0.015625+0 = -0.015625;
    #       eqvol 0-0.125+0.0625 = -0.0625; horizon 3
    #   sens_rp (03-03, 03-05]: XLK (1+0.5)(1-0.5)-1 = -0.25; DGS10 0.03125-0.015625 = 0.015625;
    #       eqvol 0.125+0.0625 = 0.1875; horizon 2
    # authored specs hash as configured; gone/sens_gone (windows outside the history) are left out.
    series = _series(XLK, DGS10, EQVOL)
    rp_a, rp_b, sens_rp = _replay("2025-03-04", "2025-03-07"), _replay("2025-03-07", "2025-03-12"), \
        _replay("2025-03-03", "2025-03-05")
    gone, sens_gone = _replay("2025-02-03", "2025-03-07"), _replay("2025-03-04", "2025-04-30")
    cat = _catalog({"rp_a": rp_a, "authored": AUTHORED, "gone": gone, "rp_b": rp_b},
                   {"sens_rp": sens_rp, "sens_gone": sens_gone, "sens_auth": SENS_AUTHORED},
                   series, unresolvable=("gone", "sens_gone"))
    want = {
        "rp_a": compute_scenario_version(_hand_resolved(
            rp_a, {"eq:XLK": -0.53125, "ir:DGS10": 0.046875, "eqvol": 0.25}, 3)),
        "authored": compute_scenario_version(AUTHORED),
        "rp_b": compute_scenario_version(_hand_resolved(
            rp_b, {"eq:XLK": -0.4375, "ir:DGS10": -0.015625, "eqvol": -0.0625}, 3)),
        "sens_rp": compute_scenario_version(_hand_resolved(
            sens_rp, {"eq:XLK": -0.25, "ir:DGS10": 0.015625, "eqvol": 0.1875}, 2)),
        "sens_auth": compute_scenario_version(SENS_AUTHORED),
    }
    got = scenario_versions(cat)
    assert got == want
    assert list(got) == ["rp_a", "authored", "rp_b", "sens_rp", "sens_auth"]   # scenarios, then sensitivities
    # the raw replays share a version (same overlays, no drivers); the resolved ones don't
    assert compute_scenario_version(rp_a) == compute_scenario_version(rp_b)
    assert got["rp_a"] != got["rp_b"]


@pytest.mark.criterion("C1")
def test_c1_point_authored_only_catalog_keeps_config_versions():
    # oracle: an authored scenario's version is compute_scenario_version of its config spec, unchanged;
    # a catalog of one scenario and one sensitivity gives exactly those two, in that order.
    series = _series(XLK, DGS10, EQVOL)
    cat = _catalog({"authored": AUTHORED}, {"sens_auth": SENS_AUTHORED}, series)
    got = scenario_versions(cat)
    assert got == {"authored": compute_scenario_version(AUTHORED),
                   "sens_auth": compute_scenario_version(SENS_AUTHORED)}
    assert list(got) == ["authored", "sens_auth"]


_XLK_ST = st.lists(st.integers(min_value=-8, max_value=8).map(lambda k: k / 16), min_size=8, max_size=8)
_DGS_ST = st.lists(st.integers(min_value=-8, max_value=8).map(lambda k: k / 64), min_size=8, max_size=8)
_VOL_ST = st.lists(st.integers(min_value=-8, max_value=8).map(lambda k: k / 32), min_size=8, max_size=8)


@st.composite
def _window(draw: st.DrawFn) -> tuple[int, int]:
    i = draw(st.integers(min_value=0, max_value=6))
    j = draw(st.integers(min_value=i + 1, max_value=7))
    return i, j


@pytest.mark.criterion("C1")
@given(xlk=_XLK_ST, dgs10=_DGS_ST, eqvol=_VOL_ST, w1=_window(), w2=_window())
def test_c1_property_windows_with_different_moves_get_different_versions(
        xlk: list[float], dgs10: list[float], eqvol: list[float], w1: tuple[int, int], w2: tuple[int, int]):
    # oracle: compute_scenario_version of the hand-resolved specs (exact-fraction moves, horizon j - i); two
    # replays over the same history whose episode moves differ get different versions.
    series = _series(xlk, dgs10, eqvol)
    s1, s2 = _replay(DAYS[w1[0]], DAYS[w1[1]]), _replay(DAYS[w2[0]], DAYS[w2[1]])
    m1, m2 = _hand_moves(xlk, dgs10, eqvol, *w1), _hand_moves(xlk, dgs10, eqvol, *w2)
    got = scenario_versions(_catalog({"one": s1, "two": s2}, {}, series))
    want1 = compute_scenario_version(_hand_resolved(s1, m1, w1[1] - w1[0]))
    want2 = compute_scenario_version(_hand_resolved(s2, m2, w2[1] - w2[0]))
    assert got == {"one": want1, "two": want2}
    if m1 != m2:
        assert got["one"] != got["two"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(xlk=_XLK_ST, dgs10=_DGS_ST, eqvol=_VOL_ST, w=_window(), narrative=st.text(max_size=20),
       trigger=st.text(max_size=20))
def test_c1_metamorphic_non_economic_keys_leave_versions_unchanged(
        xlk: list[float], dgs10: list[float], eqvol: list[float], w: tuple[int, int], narrative: str, trigger: str):
    # oracle: compute_scenario_version of the hand-resolved spec; editing narrative and trigger (on a replay and
    # on an authored scenario) gives the same versions as the base catalog.
    series = _series(xlk, dgs10, eqvol)
    base = scenario_versions(_catalog({"rp": _replay(DAYS[w[0]], DAYS[w[1]]), "authored": AUTHORED}, {}, series))
    edited_authored = dict(AUTHORED, narrative=narrative, trigger=trigger)
    edited = scenario_versions(_catalog(
        {"rp": _replay(DAYS[w[0]], DAYS[w[1]], narrative, trigger), "authored": edited_authored}, {}, series))
    want = compute_scenario_version(_hand_resolved(_replay(DAYS[w[0]], DAYS[w[1]]),
                                                   _hand_moves(xlk, dgs10, eqvol, *w), w[1] - w[0]))
    assert base == {"rp": want, "authored": compute_scenario_version(AUTHORED)}
    assert edited == base


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(xlk=_XLK_ST, dgs10=_DGS_ST, eqvol=_VOL_ST, w=_window(), data=st.data(),
       factor=st.sampled_from(FACTORS), bump=st.sampled_from([-0.125, 0.0625, 0.25]))
def test_c1_metamorphic_history_under_window_changes_version(
        xlk: list[float], dgs10: list[float], eqvol: list[float], w: tuple[int, int], data: st.DataObject,
        factor: str, bump: float):
    # oracle: compute_scenario_version of the hand-resolved specs before and after the revision. A non-zero bump
    # on one row inside (start, end] changes that factor's move (a sum moves by the bump; a product moves by the
    # bump times the other rows' 1 + r, all >= 0.5), so the version changes; a bump on a row outside the
    # window leaves the moves, and the version, as they were.
    i, j = w
    series = _series(xlk, dgs10, eqvol)
    spec = _replay(DAYS[i], DAYS[j])
    before = scenario_versions(_catalog({"rp": spec}, {}, series))["rp"]
    assert before == compute_scenario_version(_hand_resolved(spec, _hand_moves(xlk, dgs10, eqvol, i, j), j - i))

    cols = {"eq:XLK": list(xlk), "ir:DGS10": list(dgs10), "eqvol": list(eqvol)}
    inside = data.draw(st.integers(min_value=i + 1, max_value=j), label="inside")
    cols[factor][inside] += bump
    revised = _hand_moves(cols["eq:XLK"], cols["ir:DGS10"], cols["eqvol"], i, j)
    after = scenario_versions(_catalog({"rp": spec}, {}, _series(cols["eq:XLK"], cols["ir:DGS10"],
                                                                 cols["eqvol"])))["rp"]
    assert after == compute_scenario_version(_hand_resolved(spec, revised, j - i))
    assert after != before

    outside_rows = [k for k in range(len(DAYS)) if k <= i or k > j]
    if outside_rows:
        cols = {"eq:XLK": list(xlk), "ir:DGS10": list(dgs10), "eqvol": list(eqvol)}
        cols[factor][data.draw(st.sampled_from(outside_rows), label="outside")] += bump
        same = scenario_versions(_catalog({"rp": spec}, {}, _series(cols["eq:XLK"], cols["ir:DGS10"],
                                                                    cols["eqvol"])))["rp"]
        assert same == before
