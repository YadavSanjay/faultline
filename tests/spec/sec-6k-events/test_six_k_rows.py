"""sec-6k-events C2: in-window 6-Ks mapped to allowlisted items become 8-K-shaped event rows.

Oracle: hand calculation on synthetic EDGAR ``filings.recent`` blocks (parallel arrays) built in the test from
real TSM primary-document file names, classified by hand against the committed rules (the spec's Requirement
(3) order); each row's columns, weight and EDGAR filing-index ``doc_url`` are written out by hand. Properties:
rows are the block's in-window allowlisted 6-Ks as a subsequence; every weight is ``allowlist[item_codes]``;
narrowing the window never adds a row; a block of only 8-Ks gives no rows.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.news.sec_events_downloader import six_k_rows

RULES: list[dict[str, str | None]] = [
    {"pattern": "revenue", "item": None},
    {"pattern": "monthend", "item": None},
    {"pattern": "dividend", "item": None},
    {"pattern": "fsx", "item": None},
    {"pattern": "agm", "item": None},
    {"pattern": "changeof", "item": "5.02"},
    {"pattern": r"^tsm-\d{8}x6k\.htm$", "item": "2.02"},
    {"pattern": "board", "item": "8.01"},
    {"pattern": "directorsresolut", "item": "8.01"},
    {"pattern": "tosell", "item": "2.01"},
    {"pattern": "acqui", "item": "2.01"},
]

# The committed 8-K allowlist plus 2.01 at 0.5 (the spec's addition).
ALLOWLIST: dict[str, float] = {
    "2.02": 0.6, "5.02": 1.0, "4.02": 1.0, "2.06": 0.8, "1.01": 0.5, "1.02": 0.5, "1.05": 0.7,
    "2.03": 0.6, "2.04": 0.8, "7.01": 0.2, "8.01": 0.2, "2.01": 0.5,
}

# Hand classification of real TSM file names against RULES.
ITEM_OF: dict[str, str | None] = {
    "tsm-revenue20261008.htm": None,
    "tsm-monthend6kx20260924.htm": None,
    "tsm-dividendadjustmentx202.htm": None,
    "tsm-fsx20260814x6k.htm": None,
    "tsm-agmx20260604x6k.htm": None,
    "a20251105changeofnaceo.htm": "5.02",
    "a20250925changeofazceoandd.htm": "5.02",
    "tsm-20261015x6k.htm": "2.02",
    "tsm-boardx20260811.htm": "8.01",
    "a20260210directorsresoluti.htm": "8.01",
    "tsmctosell81ofvanguardinte.htm": "2.01",
    "sonysemiconductorsolutions.htm": None,
    "a20251125tsmcmediastatement.htm": None,
}
NAMES = sorted(ITEM_OF)

TICKER = "TSM"
CIK = "0001046179"
START, END = "2026-08-11", "2026-10-15"
COLUMNS = {"ticker", "cik", "filing_date", "form", "item_codes", "accession", "doc_url", "weight"}
# The EDGAR filing-index doc_url split on "/": scheme, host, Archives/edgar/data, CIK without leading zeros, ...
URL_HEAD = ["https:", "", "www.sec.gov", "Archives", "edgar", "data", "1046179"]

Filing = tuple[str, str, str, str]          # (form, filingDate, accessionNumber, primaryDocument)


def _block(filings: list[Filing]) -> dict[str, list[str]]:
    """An EDGAR ``filings.recent`` block: parallel arrays, plus fields six_k_rows ignores."""
    return {
        "accessionNumber": [f[2] for f in filings],
        "filingDate": [f[1] for f in filings],
        "reportDate": ["" for _ in filings],
        "form": [f[0] for f in filings],
        "items": ["2.02,9.01" if f[0] == "8-K" else "" for f in filings],
        "primaryDocument": [f[3] for f in filings],
        "primaryDocDescription": [f[0] for f in filings],
    }


def _acc(n: int) -> str:
    return f"0001046179-26-{n:06d}"


def _rows(filings: list[Filing], start: str = START, end: str = END) -> list[dict[str, object]]:
    return six_k_rows(_block(filings), RULES, ALLOWLIST, ticker=TICKER, cik=CIK, start=start, end=end)


BLOCK: list[Filing] = [
    ("6-K", "2026-10-16", _acc(124), "a20260210directorsresoluti.htm"),   # after end -> out
    ("6-K", "2026-10-15", _acc(123), "tsm-20261015x6k.htm"),              # end day (inclusive), 2.02
    ("6-K", "2026-10-08", _acc(121), "tsm-revenue20261008.htm"),          # routine
    ("8-K", "2026-10-01", _acc(120), "a20260210directorsresoluti.htm"),   # form 8-K -> ignored
    ("6-K/A", "2026-09-30", _acc(119), "a20251105changeofnaceo.htm"),     # 6-K/A -> ignored
    ("6-K", "2026-09-24", _acc(118), "tsm-monthend6kx20260924.htm"),      # routine
    ("4", "2026-09-20", _acc(117), "xslF345X05/wk-form4_1.xml"),          # form 4 -> ignored
    ("6-K", "2026-09-15", _acc(116), "sonysemiconductorsolutions.htm"),   # unmapped
    ("6-K", "2026-09-05", _acc(115), "a20250925changeofazceoandd.htm"),   # 5.02
    ("6-K", "2026-09-01", _acc(114), "tsmctosell81ofvanguardinte.htm"),   # 2.01
    ("6-K", "2026-08-14", _acc(110), "tsm-fsx20260814x6k.htm"),           # routine
    ("6-K", "2026-08-11", _acc(108), "tsm-boardx20260811.htm"),           # start day (inclusive), 8.01
    ("6-K", "2026-08-10", _acc(107), "tsmctosell81ofvanguardinte.htm"),   # before start -> out
]

# Hand-written expected rows for BLOCK in [START, END]: (filing_date, item_codes, accession, doc_url tail, weight).
EXPECTED_ROWS = [
    ("2026-10-15", "2.02", "0001046179-26-000123",
     ["000104617926000123", "000104617926000123-index.htm"], 0.6),
    ("2026-09-05", "5.02", "0001046179-26-000115",
     ["000104617926000115", "000104617926000115-index.htm"], 1.0),
    ("2026-09-01", "2.01", "0001046179-26-000114",
     ["000104617926000114", "000104617926000114-index.htm"], 0.5),
    ("2026-08-11", "8.01", "0001046179-26-000108",
     ["000104617926000108", "000104617926000108-index.htm"], 0.2),
]


def _check_row(row: dict[str, object], filing_date: str, item: str, accession: str, url_tail: list[str],
               weight: float) -> None:
    assert set(row) == COLUMNS
    assert row["ticker"] == TICKER
    assert row["cik"] == CIK
    assert row["form"] == "6-K"
    assert row["filing_date"] == filing_date
    assert row["item_codes"] == item
    assert row["accession"] == accession
    assert str(row["doc_url"]).split("/") == [*URL_HEAD, *url_tail]
    # weight is rounded to 4 places; the allowlist values have at most 1, so only float repr error remains
    assert row["weight"] == pytest.approx(weight, abs=1e-9)


def _hand_expected(filings: list[Filing], start: str, end: str) -> list[str]:
    """Accessions the spec keeps, by the hand labels: 6-K, in [start, end], hand-mapped to an allowlisted item."""
    return [acc for form, date, acc, doc in filings
            if form == "6-K" and start <= date <= end and ITEM_OF[doc] is not None and ITEM_OF[doc] in ALLOWLIST]


@pytest.mark.criterion("C2")
def test_block_gives_exactly_the_in_window_allowlisted_6ks() -> None:
    # oracle: hand calculation over BLOCK (each filing's fate annotated beside it); doc_url built by hand
    rows = _rows(BLOCK)
    assert len(rows) == len(EXPECTED_ROWS)
    for row, expected in zip(rows, EXPECTED_ROWS, strict=True):
        _check_row(row, *expected)


@pytest.mark.criterion("C2")
def test_window_ends_are_inclusive() -> None:
    # oracle: hand — a one-day window keeps that day's mapped 6-K; the day before/after keeps nothing more
    one_day = _rows(BLOCK, start="2026-10-15", end="2026-10-15")
    assert [r["accession"] for r in one_day] == ["0001046179-26-000123"]
    first_day = _rows(BLOCK, start="2026-08-11", end="2026-08-11")
    assert [r["accession"] for r in first_day] == ["0001046179-26-000108"]
    wider = _rows(BLOCK, start="2026-08-10", end="2026-10-16")
    assert [r["accession"] for r in wider] == [
        "0001046179-26-000124", "0001046179-26-000123", "0001046179-26-000115", "0001046179-26-000114",
        "0001046179-26-000108", "0001046179-26-000107"]


@pytest.mark.criterion("C2")
def test_rows_keep_the_block_order() -> None:
    # oracle: hand — the same filings in a shuffled (not date-sorted) order come back in that order
    shuffled = [BLOCK[9], BLOCK[2], BLOCK[11], BLOCK[1], BLOCK[3], BLOCK[8]]
    rows = _rows(shuffled)
    assert [r["accession"] for r in rows] == [
        "0001046179-26-000114", "0001046179-26-000108", "0001046179-26-000123", "0001046179-26-000115"]


@pytest.mark.criterion("C2")
def test_no_qualifying_filing_gives_an_empty_list() -> None:
    # oracle: hand — routine, unmapped, 6-K/A, 8-K and out-of-window filings only; then one mapped 6-K added
    non_qualifying = [BLOCK[0], BLOCK[2], BLOCK[3], BLOCK[4], BLOCK[7], BLOCK[12]]
    assert _rows(non_qualifying) == []
    assert [r["accession"] for r in _rows([*non_qualifying, BLOCK[8]])] == ["0001046179-26-000115"]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("bad_item", ["9.99", "8.1", "Item 5.02", ""])
def test_rule_item_outside_the_allowlist_raises(bad_item: str) -> None:
    # oracle: spec — a rule item neither null nor in the allowlist raises ValueError, whatever the block holds
    with pytest.raises(ValueError, match=r"."):  # any message: the spec fixes only the type
        six_k_rows(_block(BLOCK), [*RULES, {"pattern": "sony", "item": bad_item}], ALLOWLIST,
                   ticker=TICKER, cik=CIK, start=START, end=END)
    with pytest.raises(ValueError, match=r"."):  # any message: the spec fixes only the type
        six_k_rows(_block([BLOCK[3]]), [{"pattern": "nothing-matches-this", "item": bad_item}, *RULES],
                   ALLOWLIST, ticker=TICKER, cik=CIK, start=START, end=END)


DATES = ["2026-08-10", "2026-08-11", "2026-09-01", "2026-09-15", "2026-10-15", "2026-10-16"]
FILING_PARTS = st.tuples(st.sampled_from(["6-K", "6-K", "6-K", "6-K/A", "8-K", "4"]), st.sampled_from(DATES),
                         st.sampled_from(NAMES))
WINDOWS = st.tuples(st.sampled_from(DATES), st.sampled_from(DATES)).map(sorted)


def _filings(parts: list[tuple[str, str, str]]) -> list[Filing]:
    return [(form, date, _acc(200 + i), doc) for i, (form, date, doc) in enumerate(parts)]


@pytest.mark.criterion("C2")
@given(parts=st.lists(FILING_PARTS, max_size=12), window=WINDOWS)
def test_rows_are_the_hand_selected_6ks_with_allowlist_weights(
        parts: list[tuple[str, str, str]], window: list[str]) -> None:
    # oracle: property — rows are, in block order, exactly the hand-labelled qualifying 6-Ks (a subsequence of
    # the block's 6-Ks), and each carries allowlist[item_codes]
    filings = _filings(parts)
    rows = _rows(filings, start=window[0], end=window[1])
    assert [r["accession"] for r in rows] == _hand_expected(filings, window[0], window[1])
    by_acc = {f[2]: f for f in filings}
    for row in rows:
        _form, date, _acc_no, doc = by_acc[str(row["accession"])]
        assert row["item_codes"] == ITEM_OF[doc]
        assert row["filing_date"] == date
        assert row["form"] == "6-K"
        # weight rounded to 4 places from allowlist values with 1 decimal: equal up to float repr
        assert row["weight"] == pytest.approx(ALLOWLIST[str(row["item_codes"])], abs=1e-9)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(parts=st.lists(FILING_PARTS, max_size=12), window=WINDOWS, inner=WINDOWS)
def test_narrowing_the_window_never_adds_a_row(
        parts: list[tuple[str, str, str]], window: list[str], inner: list[str]) -> None:
    # oracle: metamorphic — a window inside another keeps a subset of its rows; the wide one matches the hand labels
    filings = _filings(parts)
    lo, hi = max(window[0], inner[0]), min(window[1], inner[1])
    wide = [r["accession"] for r in _rows(filings, start=window[0], end=window[1])]
    assert wide == _hand_expected(filings, window[0], window[1])
    if lo <= hi:
        narrow = [r["accession"] for r in _rows(filings, start=lo, end=hi)]
        assert set(narrow) <= set(wide)
        assert narrow == [a for a in wide if a in set(narrow)]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(parts=st.lists(FILING_PARTS, min_size=1, max_size=12))
def test_a_block_of_only_8ks_gives_no_rows(parts: list[tuple[str, str, str]]) -> None:
    # oracle: metamorphic — relabelling every filing 8-K empties the rows; relabelling it 6-K gives the hand labels
    as_6k = [("6-K", date, acc, doc) for _f, date, acc, doc in _filings(parts)]
    as_8k = [("8-K", date, acc, doc) for _f, date, acc, doc in as_6k]
    assert [r["accession"] for r in _rows(as_6k, start=DATES[0], end=DATES[-1])] == _hand_expected(
        as_6k, DATES[0], DATES[-1])
    assert _rows(as_8k, start=DATES[0], end=DATES[-1]) == []
