"""Unit tests for scenario-event-replay wiring (the runner's point-in-time pieces and scoring).

The locked spec tests cover the pure rules in ``event_replay.py``; these cover the runner: the factor
history cut at the as-of, XbrlRag legs pinned to filed fiscal years, episode scoring, the realised-move
panel and the committed config (synthetic inputs; no data/, no network).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from src.pricer.risk.factor_history import FactorHistory
from src.scenariogen.config import config_dir
from src.scenariogen.event_replay import event_replay_settings, load_event_replay_config
from src.scenariogen.event_replay_runner import (
    PitFactorHistory,
    _realised_panel,
    pit_arag_config,
    score_episode,
)

AS_OF = date(2025, 1, 24)


class _Synthetic(FactorHistory):
    """A factor history serving fixed series (no files)."""

    def __init__(self) -> None:
        self.cfg, self.credit_refs, self.issuer_credit_info = {}, {}, {}

    def _collect_series(self, *, include_style: bool = False) -> dict[str, pd.Series]:
        idx = pd.bdate_range("2025-01-20", "2025-01-31")
        return {"eq:XLK": pd.Series(0.01, index=idx), "credit": pd.Series(0.0001, index=idx)}


class _PitSynthetic(PitFactorHistory, _Synthetic):
    def __init__(self, cutoff: date) -> None:
        _Synthetic.__init__(self)
        self.cutoff = pd.Timestamp(cutoff)


def test_pit_history_cuts_every_series_at_the_as_of():
    series = _PitSynthetic(AS_OF).factor_series()
    for s in series.values():
        assert pd.to_datetime(s.index).max() == pd.Timestamp(AS_OF)
    assert len(_Synthetic().factor_series()["eq:XLK"]) > len(series["eq:XLK"])


def test_arag_legs_are_pinned_to_filed_fiscal_years_and_unknown_filers_dropped():
    cfg = {"connection": {}, "scenarios": {"s": {"companies": [
        {"ticker": "NVDA", "fiscal_years": [2024, 2025], "calibration": {"fiscal_year": 2025}},
        {"ticker": "MSFT", "fiscal_years": [2024, 2025]},
        {"ticker": "ZZZZ", "fiscal_years": [2025]}], "expect_tickers": ["NVDA", "MSFT", "ZZZZ"]}}}
    fye = {"NVDA": (1, 31), "MSFT": (6, 30)}
    pinned, dropped = pit_arag_config(cfg, AS_OF, fye.get)
    legs = {c["ticker"]: c for c in pinned["scenarios"]["s"]["companies"]}
    # NVDA FY2024 ended 2024-01-31, filed by 2024-04-30 <= 2025-01-24; FY2025 ends 2025-01-31: not filed.
    assert legs["NVDA"]["fiscal_years"] == [2023, 2024] and legs["NVDA"]["calibration"]["fiscal_year"] == 2024
    # MSFT FY2024 ended 2024-06-30, filed by 2024-09-28.
    assert legs["MSFT"]["fiscal_years"] == [2023, 2024]
    assert pinned["scenarios"]["s"]["expect_tickers"] == ["NVDA", "MSFT"]
    assert dropped == [{"scenario": "s", "ticker": "ZZZZ", "reason": "no fiscal year-end"}]
    assert cfg["scenarios"]["s"]["companies"][0]["fiscal_years"] == [2024, 2025]     # input untouched


def test_score_episode_scores_each_version_and_picks_the_nearest():
    realised = {"eq:A": -0.10, "eq:B": -0.05, "eq:C": 0.02, "credit": 0.002}
    scale = {"eq:A": 0.05, "eq:B": 0.05, "eq:C": 0.05, "credit": 0.001}
    vectors: dict[str, dict[str, dict[str, float]]] = {
        "authored": {"close": {"eq:A": -0.2, "eq:B": -0.1, "eq:C": 0.04, "credit": 0.004},
                     "far": {"eq:A": 0.2, "eq:B": 0.1, "eq:C": -0.04, "credit": -0.004}},
    }
    out = score_episode(vectors, realised, scale, {"eq:A": "semis", "credit": "credit"}, min_abs_z=1.0)
    assert out["authored"]["nearest"] == "close"
    assert out["authored"]["scores"]["close"]["cosine"] == pytest.approx(1.0)
    assert out["authored"]["scores"]["far"]["sleeves"]["semis"] == {"hits": 0, "n": 1, "rate": 0.0}


def test_realised_panel_is_on_the_equity_calendar():
    xlk = pd.Series([0.01, 0.02], index=pd.to_datetime(["2025-01-27", "2025-01-28"]))
    fx = pd.Series([0.001, 0.002, 0.003], index=pd.to_datetime(["2025-01-27", "2025-01-28", "2025-01-29"]))
    panel = _realised_panel({"eq:XLK": xlk, "fx:JPY": fx})
    assert list(panel.index) == list(xlk.index) and list(panel["fx:JPY"]) == [0.001, 0.002]


def test_committed_config_is_valid_and_names_the_three_episodes():
    raw: dict[str, Any] = json.loads((config_dir() / "event_replay.json").read_text(encoding="utf-8"))
    eps = load_event_replay_config(raw)
    assert [(e.name, e.as_of.isoformat(), e.horizon_days) for e in eps] == [
        ("yen_carry", "2024-07-10", 18), ("deepseek", "2025-01-24", 1), ("tariff", "2025-04-02", 4)]
    z, sleeves = event_replay_settings(raw)
    assert z == 1.0 and sleeves["eq:NVDA"] == "semis" and sleeves["credit:ORCL"] == "credit"
    assert Path(config_dir() / "event_replay.json").exists()
