"""replay-pit-leaks C2: ``event_replay.pit_issuer_drops`` lists the issuer factors a replay must drop.

Oracle: Requirement 2 by hand. For each issuer in ``issuer_info`` the key ``"credit:<ISSUER>"``
(upper-cased) is dropped when ``date.fromisoformat(dtcc_start) > cutoff``, or when ``dtcc_start`` is
missing or empty; it is kept when ``dtcc_start <= cutoff``. Empty input -> ``set()``; ``ValueError``
for a ``dtcc_start`` that is not an ISO date; the input is never mutated.
"""

from __future__ import annotations

import copy
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.event_replay import pit_issuer_drops

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

DATES = st.dates(min_value=date(2015, 1, 1), max_value=date(2030, 12, 31))
# Upper-case names keep issuers distinct after upper-casing.
ISSUERS = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=5)
# A start: an ISO date, missing (None -> key absent) or the empty string.
STARTS = st.one_of(DATES, st.none(), st.just(""))


def _info(starts: dict[str, date | str | None]) -> dict[str, dict[str, str]]:
    """Provenance per issuer: ``dtcc_start`` as ISO text, ``""``, or absent for ``None``."""
    out: dict[str, dict[str, str]] = {}
    for issuer, start in starts.items():
        prov = {"source": "dtcc_sdr"}
        if isinstance(start, date):
            prov["dtcc_start"] = start.isoformat()
        elif start == "":
            prov["dtcc_start"] = ""
        out[issuer] = prov
    return out


def _expected(starts: dict[str, date | str | None], cutoff: date) -> set[str]:
    """Requirement 2 by hand: missing/empty start or start after the cutoff -> dropped."""
    return {f"credit:{i.upper()}" for i, s in starts.items() if not isinstance(s, date) or s > cutoff}


@pytest.mark.criterion("C2")
def test_c2_point_before_on_after_missing_empty_lowercase():
    # oracle: Requirement 2 by hand, cutoff 2024-06-30:
    #   ORCL start 2025-01-02 (after)     -> dropped
    #   META start 2024-06-30 (on)        -> kept
    #   IBM  start 2023-03-01 (before)    -> kept
    #   AAPL no dtcc_start                -> dropped
    #   MSFT dtcc_start ""                -> dropped
    #   nvda start 2024-07-01 (after)     -> dropped as "credit:NVDA" (upper-cased)
    info = {
        "ORCL": {"dtcc_start": "2025-01-02", "source": "dtcc_sdr"},
        "META": {"dtcc_start": "2024-06-30", "source": "dtcc_sdr"},
        "IBM": {"dtcc_start": "2023-03-01"},
        "AAPL": {"source": "dtcc_sdr"},
        "MSFT": {"dtcc_start": ""},
        "nvda": {"dtcc_start": "2024-07-01"},
    }
    assert pit_issuer_drops(info, date(2024, 6, 30)) == {
        "credit:ORCL", "credit:AAPL", "credit:MSFT", "credit:NVDA"}


@pytest.mark.criterion("C2")
def test_c2_point_empty_input_and_start_day_boundary():
    # oracle: Requirement 2 by hand: empty issuer_info -> set(); a single issuer starting 2025-01-02
    #   is dropped at cutoff 2025-01-01 and kept at cutoff 2025-01-02 (the start day itself).
    assert pit_issuer_drops({}, date(2025, 1, 1)) == set()
    one = {"ORCL": {"dtcc_start": "2025-01-02"}}
    assert pit_issuer_drops(one, date(2025, 1, 1)) == {"credit:ORCL"}
    assert pit_issuer_drops(one, date(2025, 1, 2)) == set()


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("bad", ["2025/01/02", "2025-13-01", "Jan 2 2025", "not-a-date"])
def test_c2_point_malformed_start_raises(bad):
    # oracle: Requirement 2 by hand: date.fromisoformat rejects each of these -> ValueError.
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        pit_issuer_drops({"ORCL": {"dtcc_start": bad}}, date(2025, 6, 30))


@pytest.mark.criterion("C2")
def test_c2_point_input_not_mutated():
    # oracle: Requirement 2 by hand: at cutoff 2024-06-30 ORCL (2025-01-02) is dropped, meta
    #   (2024-01-02) kept; the mapping is unchanged after the call.
    info = {"ORCL": {"dtcc_start": "2025-01-02"}, "meta": {"dtcc_start": "2024-01-02"}}
    before = copy.deepcopy(info)
    assert pit_issuer_drops(info, date(2024, 6, 30)) == {"credit:ORCL"}
    assert info == before


@pytest.mark.criterion("C2")
@given(starts=st.dictionaries(ISSUERS, STARTS, max_size=8), cutoff=DATES, lower=st.booleans())
def test_c2_property_dropped_exactly_when_missing_empty_or_after(starts, cutoff, lower):
    # oracle: Requirement 2 by hand (_expected): an issuer is in the result exactly when its start is
    #   missing/empty or after the cutoff; issuer names may be passed lower-case, keys are upper.
    if lower:
        starts = {i.lower(): s for i, s in starts.items()}
    info = _info(starts)
    before = copy.deepcopy(info)
    assert pit_issuer_drops(info, cutoff) == _expected(starts, cutoff)
    assert info == before


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(starts=st.dictionaries(ISSUERS, STARTS, min_size=1, max_size=8), a=DATES, b=DATES)
def test_c2_metamorphic_later_cutoff_never_adds_a_key(starts, a, b):
    # oracle: Requirement 2 by hand: drops at the later cutoff are a subset of drops at the earlier
    #   one, and the later set is _expected(starts, later).
    earlier, later = min(a, b), max(a, b)
    info = _info(starts)
    late = pit_issuer_drops(info, later)
    early = pit_issuer_drops(info, earlier)
    assert late == _expected(starts, later)
    assert late <= early
