"""var-panel-window wiring: VarEngine replays the full-revaluation panel's dates.

Two I/O boundaries are replaced: `VarEngine._factor_moves` (reads the market-data CSVs) and
`VarEngine._book_sensitivities` (prices the trades), plus `FactorHistory._collect_series` (reads the
factor CSVs). The window choice, gap carry, P&L sum and result fields run for real.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.risk.historical_var import FullRevalVarResult
from src.pricer.risk.var import VarEngine

NAN = math.nan

# Full-revaluation panel dates: six business days (the factor values are irrelevant here, only the
# dates the aligned panel keeps).
_FR_DATES = pd.DatetimeIndex(["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06",
                              "2026-03-09"])

# Sensitivity-VaR factor moves. EQ has a value on a date the full-reval panel does not keep
# (2026-02-27, before the window, and the Saturday 2026-03-07) and a one-row gap on 03-04.
_EQ = pd.Series([0.9, 0.01, 0.02, NAN, -0.03, 0.04, 5.0, 0.05],
                index=pd.DatetimeIndex(["2026-02-27", "2026-03-02", "2026-03-03", "2026-03-04",
                                        "2026-03-05", "2026-03-06", "2026-03-07", "2026-03-09"]))
_FX = pd.Series([0.001, -0.002, 0.003, 0.0, -0.001, 0.002], index=_FR_DATES)

_BOOK = {"EQUITY_DELTA:AAA": 100.0, "FX_DELTA:JPY": 1000.0}


class _SyntheticHistory(FactorHistory):
    def _collect_series(self, *, include_style: bool = False) -> dict[str, pd.Series]:
        return {"eq:AAA": pd.Series(0.01, index=_FR_DATES), "fx:JPY": pd.Series(0.001, index=_FR_DATES)}


class _Engine(VarEngine):
    def _factor_moves(self, name: str, fcfg: dict[str, Any]) -> pd.Series | None:
        return {"EQ_AAA": _EQ, "FX_JPY": _FX}[name].dropna()

    def _book_sensitivities(self, trades: list, ctx: ValuationContext) -> dict[str, float]:
        return dict(_BOOK)


def _config(lookback: int) -> dict[str, Any]:
    return {"risk": {"var": {
        "lookback_days": lookback, "horizon_days": 1, "confidence_levels": [0.95],
        "factors": {"EQ_AAA": {"maps_to": "EQUITY_DELTA:AAA"}, "FX_JPY": {"maps_to": "FX_DELTA:JPY"}},
        "fhs": {"enabled": True, "lambda": 0.94}, "stressed": {"enabled": True, "stress_window": 20},
    }}}


def _engine(tmp_path: Path, lookback: int) -> _Engine:
    cfg = _config(lookback)
    ve = _Engine(None, cfg, tmp_path)  # type: ignore[arg-type]  # risk engine unused: sensitivities are injected
    ve._history = _SyntheticHistory(tmp_path, cfg)
    return ve


def _ctx() -> ValuationContext:
    return ValuationContext(valuation_date=date(2026, 3, 9))


def test_headline_uses_the_full_reval_window_and_carries_the_gap(tmp_path):
    r = _engine(tmp_path, lookback=5).historical_var([], _ctx(), include_gamma=False)
    # oracle: the last 5 full-reval dates; EQ's 03-04 hole is a 1-row interior gap -> carried, not dropped
    assert r.dates == ["2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09"]
    assert r.n_scenarios == 5
    # oracle: per day 100*eq + 1000*fx, with eq = 0.0 on the carried 03-04 (horizon 1 -> no scaling)
    #   date   eq      fx       100*eq + 1000*fx
    #   03-03  0.02   -0.002    0.0
    #   03-04  0.0     0.003    3.0   (eq carried)
    #   03-05 -0.03    0.0     -3.0
    #   03-06  0.04   -0.001    3.0
    #   03-09  0.05    0.002    7.0
    expected = [100 * 0.02 + 1000 * -0.002, 100 * 0.0 + 1000 * 0.003, 100 * -0.03 + 1000 * 0.0,
                100 * 0.04 + 1000 * -0.001, 100 * 0.05 + 1000 * 0.002]
    assert r.pnl_vector == pytest.approx(expected, abs=1e-12)  # sums of two products: rounding only


def test_values_outside_the_window_are_not_replayed(tmp_path):
    r = _engine(tmp_path, lookback=6).historical_var([], _ctx(), include_gamma=False)
    # oracle: 02-27 (before the window) and the Saturday 03-07 are not full-reval dates
    assert r.dates == [d.date().isoformat() for d in _FR_DATES]
    assert max(abs(x) for x in r.pnl_vector) < 10.0   # EQ's 0.9 and 5.0 would add 90 / 500


def test_result_dicts_carry_dates(tmp_path):
    r = _engine(tmp_path, lookback=4).historical_var([], _ctx(), include_gamma=False)
    d = r.to_dict()
    assert d["dates"] == r.dates and len(d["dates"]) == len(d["pnl_vector"])
    fr = FullRevalVarResult("2026-03-09", 4, 1, 1, 0.0, dates=["2026-03-09"])
    assert fr.to_dict()["dates"] == ["2026-03-09"]


def test_fhs_uses_the_same_window(tmp_path):
    r = _engine(tmp_path, lookback=5).historical_var([], _ctx(), include_gamma=False)
    # oracle: FHS replays the headline's 5-date window (EWMA scaling keeps every observed row)
    assert r.fhs["n_scenarios"] == 5


def test_all_history_panel_covers_every_full_reval_date(tmp_path):
    ve = _engine(tmp_path, lookback=5)
    cols = {"EQ_AAA": _EQ.dropna(), "FX_JPY": _FX}
    # oracle: stressed VaR searches the whole aligned history: all six full-reval dates, gap carried
    assert list(ve._panel(cols, None).index) == list(_FR_DATES)


def test_empty_window_keeps_the_empty_result(tmp_path):
    ve = _engine(tmp_path, lookback=4)
    ve._history_dates = pd.DatetimeIndex([])
    r = ve.historical_var([], _ctx(), include_gamma=False)
    # oracle: today's behaviour for an empty panel
    assert r.n_scenarios == 0 and r.pnl_vector == [] and r.dates == []
