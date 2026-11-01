"""panel-gap-carry wiring: validated panel config + carry in FactorHistory._aligned_moves + VaR provenance.

The factor loaders read market-data files (an I/O boundary), so `_SyntheticHistory` overrides only
`_collect_series` with hand-built move series; the alignment, carry and provenance code runs for real.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from src.pricer.config import load_pricer_config
from src.pricer.config_models import ConfigError, risk_config, validate_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.risk.historical_var import FullRevalVarEngine

NAN = math.nan

# Moves by factor over the union of dates (NaN = the factor has no move that date).
#   date          A     B     C     D      note
_ROWS = [
    ("2026-03-02", 0.1, NAN, 0.3, 0.4),   # B missing (1-day gap; B is observed before and after)
    ("2026-03-03", 0.2, 0.5, 0.1, 0.2),
    ("2026-03-04", 0.3, NAN, 0.2, 0.1),   # B 1-day gap
    ("2026-03-05", 0.4, 0.6, 0.3, 0.2),
    ("2026-03-06", NAN, NAN, NAN, 0.9),   # observed by D only: ineligible at 0.75
    ("2026-03-09", 0.5, 0.7, NAN, 0.3),   # C gap 1/2
    ("2026-03-10", 0.6, 0.8, NAN, 0.4),   # C gap 2/2
    ("2026-03-11", 0.7, 0.9, 0.4, 0.5),
]


def _series() -> dict[str, pd.Series]:
    index = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in _ROWS])
    cols = {name: [r[1 + j] for r in _ROWS] for j, name in enumerate("ABCD")}
    return {name: pd.Series(vals, index=index, dtype=float).dropna() for name, vals in cols.items()}


class _SyntheticHistory(FactorHistory):
    def _collect_series(self, *, include_style: bool = False) -> dict[str, pd.Series]:
        return _series()


def _cfg(**risk: object) -> dict:
    return {"risk": dict(risk)}


# ----------------------------------------------------------------- config


def test_panel_config_defaults_and_committed_values():
    # oracle: the spec's defaults (3 business days, 0.75 coverage)
    assert risk_config({}).panel_max_gap_days == 3
    assert risk_config({}).panel_min_coverage == pytest.approx(0.75, abs=0.0)
    committed = risk_config(load_pricer_config())
    assert committed.panel_max_gap_days == 3
    assert committed.panel_min_coverage == pytest.approx(0.75, abs=0.0)


@pytest.mark.parametrize("risk", [
    {"panel_max_gap_days": "three"},
    {"panel_max_gap_days": 2.5},
    {"panel_max_gap_days": -1},
    {"panel_min_coverage": 0.0},
    {"panel_min_coverage": 1.5},
    {"panel_min_coverage": "most"},
    {"panel_max_gap_day": 3},          # misspelt key
])
def test_bad_panel_config_raises_config_error(risk):
    with pytest.raises(ConfigError):
        validate_config({"risk": risk})
    with pytest.raises(ConfigError):
        FactorHistory(Path("."), {"risk": risk})


# ----------------------------------------------------------------- wiring


def test_max_gap_zero_reproduces_the_plain_inner_join():
    # oracle: the pre-change expression, computed here: concat then dropna(how="any")
    expected = pd.concat(_series(), axis=1).dropna(how="any")
    fh = _SyntheticHistory(Path("."), _cfg(panel_max_gap_days=0))
    out = fh.aligned_panel(lookback=100)
    pd.testing.assert_frame_equal(out, expected, check_exact=True)
    assert fh.panel_carried_cells == {"A": 0, "B": 0, "C": 0, "D": 0}


def test_default_carry_fills_short_gaps_and_drops_ineligible_dates():
    # oracle: by hand on _ROWS with max_gap 3, min_coverage 0.75: 03-06 (1/4 observed) is removed; B's
    #   1-day gap on 03-04 and C's 2-day gap on 03-09/10 become 0.0 moves; B's leading NaN on 03-02 stays
    #   NaN, so dropna removes 03-02.
    fh = _SyntheticHistory(Path("."), {})
    out = fh.aligned_panel(lookback=100)
    assert [str(d.date()) for d in out.index] == [
        "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-09", "2026-03-10", "2026-03-11"]
    assert out.loc["2026-03-04", "B"] == pytest.approx(0.0, abs=0.0)   # exact literal fill
    assert list(out.loc[["2026-03-09", "2026-03-10"], "C"]) == [0.0, 0.0]
    assert out.loc["2026-03-11", "D"] == pytest.approx(0.5, abs=0.0)   # observed values copied
    assert fh.panel_carried_cells == {"A": 0, "B": 1, "C": 2, "D": 0}


def test_carried_cells_count_only_the_lookback_window():
    # oracle: the last 2 rows (03-10, 03-11) hold one carried cell (C on 03-10)
    fh = _SyntheticHistory(Path("."), {})
    out = fh.aligned_panel(lookback=2)
    assert len(out) == 2
    assert fh.panel_carried_cells == {"A": 0, "B": 0, "C": 1, "D": 0}


def test_min_coverage_from_config_is_applied():
    # oracle: by hand: at coverage 0.25, 03-06 (1/4) is eligible. A's 1-row gap (03-06) fills; B fills
    #   03-04 and 03-06 (its leading 03-02 stays NaN and is dropped); C's run 03-06..03-10 is 3 rows -> fills.
    fh = _SyntheticHistory(Path("."), _cfg(panel_min_coverage=0.25))
    out = fh.aligned_panel(lookback=100)
    assert "2026-03-06" in {str(d.date()) for d in out.index}
    assert fh.panel_carried_cells == {"A": 1, "B": 2, "C": 3, "D": 0}


def test_var_result_records_the_carried_cells():
    # An empty book still builds the shocks; the result carries the window's carry provenance.
    ve = FullRevalVarEngine(None, {}, Path("."))  # engine unused: no trades to reprice
    ve.history = _SyntheticHistory(Path("."), {})
    r = ve.historical_var([], ValuationContext(valuation_date=date(2026, 3, 11)), lookback=100)
    assert r.n_scenarios == 6
    assert r.panel_carried_cells == {"A": 0, "B": 1, "C": 2, "D": 0}
    assert r.to_dict()["panel_carried_cells"] == {"A": 0, "B": 1, "C": 2, "D": 0}
