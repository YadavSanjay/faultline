"""issuer-credit-factors C4: ``bumps.shift_credit_issuers``, the per-issuer credit spread shift.

Oracle: Requirement 4 by hand: each ``CreditSpreadData`` (by ``issuer``) and ``CdsSpreadData`` (by
``reference``) gets ``shifts.get(name, default)`` added to ``spread`` and to every ``curve_points`` spread;
every other field and every other item is unchanged; with no shifts it equals ``shift_credit(default)``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.risk.bumps import shift_credit, shift_credit_issuers

CREDIT_KEYS = ("bond_orcl", "bond_aapl", "bond_meta", "cds_meta", "cds_orcl", "cds_cdx")


@pytest.mark.criterion("C4")
def test_c4_point_per_issuer_and_default(h):
    # oracle: hand calculation, shifts {ORCL: +0.0100, META: +0.0020}, default +0.0005:
    #   bond_orcl 0.0240 -> 0.0340, curve (1Y .0150, 5Y .0240, 10Y .0280) -> (.0250, .0340, .0380);
    #   cds_orcl 0.0246 -> 0.0346 (no curve); bond_meta 0.0096 -> 0.0116; cds_meta 0.0096 -> 0.0116 and
    #   curve (.0080, .0096, .0110) -> (.0100, .0116, .0130); bond_aapl 0.0045 -> 0.0050 (default);
    #   cds_cdx 0.0055 -> 0.0060 (default; index, still shifted like shift_credit).
    items = h.make_items()
    tf = shift_credit_issuers({"ORCL": 0.0100, "META": 0.0020}, 0.0005)
    out = {k: tf(v) for k, v in items.items()}
    want_d = {"bond_orcl": 0.0100, "cds_orcl": 0.0100, "bond_meta": 0.0020, "cds_meta": 0.0020,
              "bond_aapl": 0.0005, "cds_cdx": 0.0005}
    for key, d in want_d.items():
        h.assert_shifted(out[key], items[key], d, rel=1e-12)  # spec rel 1e-12
    assert out["bond_orcl"].spread == pytest.approx(0.0340, rel=1e-12)  # spec rel 1e-12
    assert [s for _, s in out["cds_meta"].curve_points] == pytest.approx([0.0100, 0.0116, 0.0130], rel=1e-12)
    assert out["spot_orcl"] == items["spot_orcl"]  # an equity spot named ORCL is not a credit item
    assert out["plain"] == "credit:ORCL"


@pytest.mark.criterion("C4")
def test_c4_point_unknown_names_take_default(h):
    # oracle: hand calculation: shifts name only an issuer absent from the items (TSLA), so every credit
    #   item takes the default -0.0010 (a tightening): bond_aapl 0.0045 -> 0.0035, cds_cdx 0.0055 -> 0.0045.
    items = h.make_items()
    tf = shift_credit_issuers({"TSLA": 0.05}, -0.0010)
    for key in CREDIT_KEYS:
        h.assert_shifted(tf(items[key]), items[key], -0.0010, rel=1e-12)  # spec rel 1e-12
    assert tf(items["bond_aapl"]).spread == pytest.approx(0.0035, rel=1e-12)  # spec rel 1e-12
    assert tf(items["spot_orcl"]) == items["spot_orcl"]


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("d", [0.0005, -0.0012, 0.0250])
def test_c4_point_empty_shifts_equals_shift_credit(h, d):
    # oracle: Requirement 4: shift_credit_issuers({}, d) behaves exactly as the existing shift_credit(d);
    #   anchored to the hand value spread + d for each credit item.
    items = h.make_items()
    new, old = shift_credit_issuers({}, d), shift_credit(d)
    for item in items.values():
        assert new(item) == old(item)
    for key in CREDIT_KEYS:
        h.assert_shifted(new(items[key]), items[key], d, rel=1e-12)  # spec rel 1e-12


_shift = st.floats(min_value=-0.02, max_value=0.05)
_shifts = st.dictionaries(st.sampled_from(["ORCL", "META", "AAPL", "TSLA"]), _shift, max_size=4)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(a=_shifts, da=_shift, b=_shifts, db=_shift)
def test_c4_metamorphic_composition_adds(h, a, da, b, db):
    # oracle: Requirement 4: additive shifts compose: applying a then b equals applying a + b per name
    #   (names missing from one side use that side's default); both equal the hand base + a_n + b_n.
    items = h.make_items()
    names = set(a) | set(b)
    ab = {n: a.get(n, da) + b.get(n, db) for n in names}
    ta, tb, tab = shift_credit_issuers(a, da), shift_credit_issuers(b, db), shift_credit_issuers(ab, da + db)
    for key in CREDIT_KEYS:
        item = items[key]
        name = h.name_of(item)
        total = a.get(name, da) + b.get(name, db)
        h.assert_shifted(tb(ta(item)), item, total, rel=1e-12)  # spec rel 1e-12 (+abs 1e-15 for ~0 sums)
        h.assert_shifted(tab(item), item, total, rel=1e-12)  # spec rel 1e-12
    for key in ("spot_orcl", "plain"):
        assert tb(ta(items[key])) == items[key]
