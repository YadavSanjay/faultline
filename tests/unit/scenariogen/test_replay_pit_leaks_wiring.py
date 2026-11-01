"""replay-pit-leaks wiring: the as-of Σ regime and the dropped issuer factors of the event replay.

The locked spec tests cover the pure rules (``series_as_of``, ``pit_issuer_drops``); these cover the
wiring: ``ImpliedCorrelationRegime.resolve`` / ``resolve_regime`` read the level on or before ``as_of``,
``ScenarioCatalog.from_config`` passes a point-in-time history's cutoff, ``PitFactorHistory`` drops issuer
factors whose DTCC history starts after the cutoff, and the episode's PIT provenance (synthetic series,
temp project root; no data/, no network).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from src.pricer.risk.factor_history import FactorHistory
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.event_replay_runner import PitFactorHistory, pit_provenance
from src.scenariogen.implied_corr import ImpliedCorrelationRegime, resolve_regime

# COR3M closes on six business days; 2026-01-06 is the replay cutoff below.
COR_DATES = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
COR_CLOSE = [20.0, 30.0, 10.0, 40.0, 50.0, 60.0]
CUTOFF = date(2026, 1, 6)
REGIME_CFG = {"enabled": True, "source": "COR3M", "shrink_systemic": 0.4, "shrink_idiosyncratic": 0.6,
              "systemic_pct": 0.66, "idiosyncratic_pct": 0.34}


def _regime() -> ImpliedCorrelationRegime:
    return ImpliedCorrelationRegime(source="COR3M", series=pd.Series(COR_CLOSE, index=COR_DATES),
                                    shrink_systemic=0.4, shrink_idiosyncratic=0.6, systemic_pct=0.66,
                                    idiosyncratic_pct=0.34)


def _write_root(root: Path) -> Path:
    """A temp project root with the COR3M history and minimal scenariogen configs."""
    vol = root / "data" / "marketdata" / "options" / "vol_indices"
    vol.mkdir(parents=True)
    pd.DataFrame({"date": COR_DATES, "close": COR_CLOSE}).to_csv(vol / "COR3M_History.csv", index=False)
    cfg = root / "configs" / "scenariogen"
    cfg.mkdir(parents=True)
    (cfg / "covariance.json").write_text(json.dumps(
        {"shrinkage": 0.5, "lookback_days": 250, "implied_corr_regime": REGIME_CFG}), encoding="utf-8")
    (cfg / "scenarios.json").write_text(json.dumps({"scenarios": {}}), encoding="utf-8")
    return root


class _Synthetic(FactorHistory):
    """A factor history serving fixed series (no files); fills ``issuer_credit_info`` like the base."""

    def __init__(self) -> None:
        self.cfg, self.credit_refs, self.issuer_credit_info = {}, {}, {}

    def _collect_series(self, *, include_style: bool = False) -> dict[str, pd.Series]:
        idx = pd.bdate_range("2025-12-01", "2026-01-30")
        rng = np.random.default_rng(7)
        self.issuer_credit_info = {"ORCL": {"dtcc_start": "2026-01-07"}, "META": {"dtcc_start": "2025-12-15"}}
        return {k: pd.Series(rng.normal(0.0, 0.01, len(idx)), index=idx)
                for k in ("eq:XLK", "credit", "credit:ORCL", "credit:META")}


class _PitSynthetic(PitFactorHistory, _Synthetic):
    def __init__(self, cutoff: date) -> None:
        _Synthetic.__init__(self)
        self.cutoff = pd.Timestamp(cutoff)
        self.dropped_issuer_factors = []


def test_resolve_without_as_of_reads_the_latest_level():
    state = _regime().resolve()
    # oracle: latest close 60 is the maximum of six -> percentile 6/6 = 1 -> systemic, shrinkage 0.4.
    assert state.asof == "2026-01-09" and state.value == 60.0 and state.n_obs == 6
    assert state.percentile == pytest.approx(1.0) and state.regime == "systemic"
    assert state.shrinkage == pytest.approx(0.4)   # tolerance: default float compare of 0.6 - 1*(0.2)


def test_resolve_as_of_reads_the_level_known_then_and_cuts_before_the_lookback():
    state = _regime().resolve(as_of=CUTOFF)
    # oracle: by 2026-01-06 the closes are 20, 30, 10 -> latest 10, percentile 1/3 -> idiosyncratic.
    assert state.asof == "2026-01-06" and state.value == 10.0 and state.n_obs == 3
    assert state.percentile == pytest.approx(1 / 3) and state.regime == "idiosyncratic"
    tail = _regime().resolve(lookback=2, as_of=CUTOFF)
    # oracle: the lookback tail is taken after the cut: closes 30, 10 -> latest 10, percentile 1/2.
    assert tail.n_obs == 2 and tail.value == 10.0 and tail.percentile == pytest.approx(0.5)


def test_resolve_as_of_before_every_level_raises():
    with pytest.raises(ValueError, match="COR3M"):
        _regime().resolve(as_of=date(2025, 12, 31))


def test_resolve_regime_passes_as_of_and_disables_without_two_levels(tmp_path, monkeypatch):
    monkeypatch.delenv("PRICER_DATA_URI", raising=False)
    root = _write_root(tmp_path)
    assert resolve_regime(root, REGIME_CFG, 0.5).asof == "2026-01-09"
    assert resolve_regime(root, REGIME_CFG, 0.5, as_of=CUTOFF).asof == "2026-01-06"
    early = resolve_regime(root, REGIME_CFG, 0.5, as_of=date(2026, 1, 2))   # one level by then
    assert not early.enabled and early.shrinkage == 0.5 and early.source == "COR3M"


def test_catalog_reads_the_regime_at_a_pit_history_cutoff_only(tmp_path, monkeypatch):
    monkeypatch.delenv("PRICER_DATA_URI", raising=False)
    root = _write_root(tmp_path)
    live = ScenarioCatalog.from_config(_Synthetic(), project_root=root)
    assert live.covariance.regime is not None and live.covariance.regime["asof"] == "2026-01-09"
    pit_history = _PitSynthetic(CUTOFF)
    pit = ScenarioCatalog.from_config(pit_history, project_root=root)
    assert pit.covariance.regime is not None and pit.covariance.regime["asof"] == "2026-01-06"
    assert pit_provenance(pit, pit_history) == {"regime_asof": "2026-01-06",
                                                "dropped_issuer_factors": ["credit:ORCL"]}


def test_pit_history_drops_issuers_whose_dtcc_history_starts_after_the_cutoff():
    pit = _PitSynthetic(CUTOFF)
    series = pit.factor_series()
    # ORCL's DTCC history starts 2026-01-07 > cutoff -> dropped; META (2025-12-15) kept and cut.
    assert set(series) == {"eq:XLK", "credit", "credit:META"}
    assert pit.dropped_issuer_factors == ["credit:ORCL"]
    assert all(pd.to_datetime(s.index).max() == pd.Timestamp(CUTOFF) for s in series.values())
    assert "credit:ORCL" in _Synthetic().factor_series()           # the live history keeps it
    later = _PitSynthetic(date(2026, 1, 7))
    assert "credit:ORCL" in later.factor_series() and later.dropped_issuer_factors == []
