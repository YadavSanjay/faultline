"""sdr-dedup C8: filter_sofr_swap_records keeps every USD SOFR non-option row, any action type."""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.rates.sdr_records import filter_sofr_swap_records

DAY = "2026-10-01"
KEYWORDS = ["SOFR", "Secured Overnight"]
PREMIUM = "Option Premium Amount"
DROPPED_COL = "Spread-Leg 2"          # absent from the raw file -> must come back present and blank
EXTRA_COL = "Custom basket indicator"  # in the raw file, not an output column -> must not come back


def _raw_row(sdr, did, ccy, underlier, premium, action):
    rec = sdr.record(did, action=action, fields={"Notional currency-Leg 1": ccy, "UPI Underlier Name": underlier})
    del rec["date"]
    del rec[DROPPED_COL]
    rec[PREMIUM] = premium
    rec[EXTRA_COL] = "FALSE"
    return rec


def _is_blank(v):
    return v is None or v == "" or (not isinstance(v, str) and pd.isna(v))


# (id, currency, underlier, option premium, action type, keep?)
LABELLED = [
    ("9000000000000000001", "USD", "USD-SOFR-OIS Compound", None, "NEWT", True),
    ("9000000000000000002", "USD", "USD-SOFR-COMPOUND", "", "CORR", True),          # '' premium is blank
    ("9000000000000000003", "USD", "usd-sofr-ois compound", None, "EROR", True),    # case-insensitive
    ("9000000000000000004", "USD", "USD-SOFR-OIS Compound", None, "TERM", True),
    ("9000000000000000005", "USD", "USD-SOFR-OIS Compound", None, "MODI", True),
    ("9000000000000000006", "USD", "Secured Overnight Financing Rate", None, "REVI", True),  # 2nd keyword
    ("9000000000000000007", "EUR", "USD-SOFR-OIS Compound", None, "NEWT", False),   # wrong currency
    ("9000000000000000008", "USD", "USD-LIBOR-BBA", None, "NEWT", False),           # not SOFR
    ("9000000000000000009", "USD", "USD-SOFR-OIS Compound", "125000", "NEWT", False),  # option
    ("9000000000000000010", "USD", "USD-Federal Funds-H.15-OIS-COMPOUND", None, "NEWT", False),
    ("9000000000000000011", "USD", None, None, "NEWT", False),                      # no underlier
]


@pytest.mark.criterion("C8")
def test_c8_labelled_rows_and_output_schema(sdr):
    # oracle: hand-built raw frame with labelled keep/drop rows (LABELLED) and the Interface column list.
    raw = pd.DataFrame([_raw_row(sdr, *row[:5]) for row in LABELLED])
    out = filter_sofr_swap_records(raw, DAY, list(KEYWORDS), "USD")
    keep = [row[0] for row in LABELLED if row[5]]
    assert sdr.ids(out) == sorted(keep)
    assert sorted(out.columns) == sorted(sdr.OUTPUT_COLUMNS)
    assert len(out.columns) == len(sdr.OUTPUT_COLUMNS)
    assert all(v == DAY for v in out["date"])
    assert all(_is_blank(v) for v in out[DROPPED_COL])
    carried = [c for c in sdr.OUTPUT_COLUMNS if c not in ("date", DROPPED_COL)]
    assert sdr.canon(out, carried) == sdr.canon(raw[raw[sdr.DID].isin(keep)], carried)


ROW = st.tuples(
    st.sampled_from(["USD", "EUR", "GBP"]),
    st.sampled_from(["USD-SOFR-OIS Compound", "usd-sofr-compound", "USD-LIBOR-BBA", "EUR-EuroSTR-OIS Compound",
                     "Secured Overnight Financing Rate", None]),
    st.sampled_from([None, "", "50000"]),
    st.sampled_from(["NEWT", "MODI", "TERM", "REVI", "CORR", "EROR", "PRTO"]),
)


@pytest.mark.criterion("C8")
@given(rows=st.lists(ROW, max_size=25), perm_seed=st.randoms(use_true_random=False))
def test_c8_property_predicate_and_row_order(sdr, rows, perm_seed):
    # oracle: the C8 predicate written out here (currency equal, any keyword case-insensitively in the
    # underlier, blank premium); metamorphic: shuffling the raw rows does not change the kept set.
    specs = [(f"91000000000000000{k:02d}", *r) for k, r in enumerate(rows)]
    specs.append(("9199999999999999999", "USD", "USD-SOFR-OIS Compound", None, "CORR"))  # always kept
    raw = pd.DataFrame([_raw_row(sdr, *s) for s in specs])

    def keep(ccy, underlier, premium):
        named = underlier is not None and any(k.lower() in underlier.lower() for k in KEYWORDS)
        return ccy == "USD" and named and _is_blank(premium)

    expected = sorted(s[0] for s in specs if keep(*s[1:4]))
    out = filter_sofr_swap_records(raw, DAY, list(KEYWORDS), "USD")
    assert sdr.ids(out) == expected
    shuffled = raw.sample(frac=1.0, random_state=perm_seed.randint(0, 2**31 - 1))
    assert sdr.ids(filter_sofr_swap_records(shuffled, DAY, list(KEYWORDS), "USD")) == expected
