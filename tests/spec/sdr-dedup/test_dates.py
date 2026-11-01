"""sdr-dedup C6: sdr_dates_to_fetch re-fetches everything for an empty/legacy file, else only gaps."""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.rates.sdr_records import sdr_dates_to_fetch

# Deliberately not in calendar order: the result must keep the input order.
WINDOW = ["2026-10-02", "2026-09-30", "2026-10-01", "2026-10-05"]
DATE_POOL = [f"2026-09-{d:02d}" for d in range(1, 31)] + [f"2026-10-{d:02d}" for d in range(1, 6)]


def _legacy(dates):
    # Collapsed pre-change layout: a date column but no Dissemination Identifier column.
    return pd.DataFrame({"date": list(dates), "Fixed rate-Leg 1": ["0.0375"] * len(dates)})


@pytest.mark.criterion("C6")
def test_c6_empty_existing_fetches_every_date_in_order(sdr):
    # oracle: hand-built fixture: nothing on disk -> the whole window, input order.
    assert sdr_dates_to_fetch(sdr.empty(), list(WINDOW)) == WINDOW


@pytest.mark.criterion("C6")
def test_c6_legacy_existing_fetches_every_date_even_if_present(sdr):
    # oracle: hand-built fixture: a legacy file covering all four dates is ignored -> the whole window.
    assert sdr_dates_to_fetch(_legacy(WINDOW), list(WINDOW)) == WINDOW


@pytest.mark.criterion("C6")
def test_c6_keyed_existing_fetches_only_missing_dates(sdr):
    # oracle: hand-built fixture: records (several per day) on 2026-09-30 and 2026-10-01 -> fetch
    # 2026-10-02 and 2026-10-05, in input order.
    existing = sdr.frame([
        sdr.record("8000000000000000001", date="2026-09-30", ts="2026-09-30T10:00:00Z"),
        sdr.record("8000000000000000002", date="2026-09-30", ts="2026-09-30T11:00:00Z"),
        sdr.record("8000000000000000003", date="2026-10-01", ts="2026-10-01T10:00:00Z"),
    ])
    assert sdr_dates_to_fetch(existing, list(WINDOW)) == ["2026-10-02", "2026-10-05"]


@st.composite
def _window_and_present(draw):
    window = draw(st.lists(st.sampled_from(DATE_POOL), min_size=1, max_size=15, unique=True))
    missing = draw(st.lists(st.sampled_from(window), min_size=1, unique=True))
    present = [d for d in DATE_POOL if d not in missing and draw(st.booleans())]
    return window, present


@pytest.mark.criterion("C6")
@given(data=_window_and_present())
def test_c6_property_set_identity(sdr, data):
    # oracle: set identity: keyed file -> [d for d in dates if d not in existing dates]; legacy -> all dates.
    window, present = data
    existing = sdr.frame(sdr.record(f"81000000000000000{k:02d}", date=d, ts=f"{d}T12:00:00Z")
                         for k, d in enumerate(present))
    expected = [d for d in window if d not in set(present)]
    assert sdr_dates_to_fetch(existing, list(window)) == expected
    assert sdr_dates_to_fetch(_legacy(present), list(window)) == window
