"""Unit tests for options-shock-sizing wiring (config, PIT provider, option anchors, report flags).

The locked spec tests cover the pure functions in ``options/sizing.py``; these cover how a run applies
them: the config, the point-in-time option provider with its vol-index fallback, the option anchors of
a ``"source": "options"`` scenario, ``anchor_tier`` with those anchors, the option-tail flag and the
concentration table (synthetic files under tmp_path; a toy covariance; no data/, no QuantLib).
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
from src.common.config.models import ConfigError
from src.pricer.input.cash_equity.instruments import CashEquityTrade
from src.scenariogen.anchoring import AnchorBook, SeverityConfig, anchor_tier, option_anchors, severity_spec
from src.scenariogen.config import load_scenario_config
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.options.provider import INDEX_T_YEARS, OptionSignalProvider
from src.scenariogen.options.sizing import OptionSizingConfig, lognormal_quantile, lognormal_tail_prob
from src.scenariogen.runner import _option_tail_flag, stock_concentration
from src.scenariogen.scenario_gen import ScenarioDefinition, ScenarioGenerator

AS_OF = date(2026, 10, 6)
CFG = SeverityConfig()
BOUNDS = {"equity": {"max_abs_pct": 96}}


# --- config ------------------------------------------------------------------------------------ #
def test_committed_config_and_keyman_nvda_is_option_anchored():
    cfg = OptionSizingConfig.load()
    assert (cfg.mild_q, cfg.adverse_q, cfg.check_q, cfg.max_staleness_days) == (0.05, 0.01, 0.01, 30)
    assert cfg.vol_index["GOOGL"] == "VXGOG" and "NVDA" not in cfg.vol_index
    spec = load_scenario_config()["scenarios"]["keyman_nvda"]
    assert severity_spec(spec) == {"lead": "eq:NVDA", "direction": "down", "source": "options"}
    assert spec["horizon_days"] == 21


@pytest.mark.parametrize("raw", [{"mild_qq": 0.05}, {"mild_q": 1.2}, {"mild_q": 0.01, "adverse_q": 0.05}])
def test_config_rejects_unknown_keys_and_bad_quantiles(tmp_path, raw):
    d = tmp_path / "configs" / "scenariogen"
    d.mkdir(parents=True)
    (d / "options_sizing.json").write_text(json.dumps(raw))
    with pytest.raises(ConfigError):
        OptionSizingConfig.load(tmp_path)


# --- PIT provider ------------------------------------------------------------------------------ #
def _write_options(root, *, vxgog: float | None = 30.0) -> None:
    base = root / "data" / "marketdata" / "options"
    (base / "signals").mkdir(parents=True)
    (base / "vol_indices").mkdir(parents=True)
    pd.DataFrame([
        {"snapshot_date": "2026-09-20", "underlying": "NVDA", "atm_iv_30d": 0.30, "rnd_spot": 100.0},
        {"snapshot_date": "2026-10-16", "underlying": "NVDA", "atm_iv_30d": 0.50, "rnd_spot": 200.0},
        {"snapshot_date": "2026-07-01", "underlying": "AMD", "atm_iv_30d": 0.40, "rnd_spot": 50.0},
    ]).to_csv(base / "signals" / "option_signals.csv", index=False)
    strikes = np.array([80.0, 90.0, 100.0, 110.0, 120.0])
    rows = [{"underlying": "NVDA", "snapshot_date": "2026-09-20", "dte": 30, "strike": k, "pdf": 0.0, "cdf": c}
            for k, c in zip(strikes, [0.0, 0.02, 0.4, 0.9, 1.0], strict=True)]
    rows += [{"underlying": "NVDA", "snapshot_date": "2026-10-16", "dte": 30, "strike": 2 * k, "pdf": 0.0,
              "cdf": c} for k, c in zip(strikes, [0.0, 0.5, 0.6, 0.9, 1.0], strict=True)]
    pd.DataFrame(rows).to_csv(base / "signals" / "rnd_curves.csv", index=False)
    if vxgog is not None:
        pd.DataFrame([{"date": "2026-10-02", "close": vxgog}, {"date": "2026-10-09", "close": 99.0}]).to_csv(
            base / "vol_indices" / "VXGOG_History.csv", index=False)


def test_provider_is_point_in_time_and_falls_back_to_the_vol_index(tmp_path):
    _write_options(tmp_path)
    p = OptionSignalProvider.load(tmp_path, None, as_of=AS_OF, sizing=OptionSizingConfig())
    assert p is not None
    assert p.atm_iv("NVDA") == pytest.approx(0.30)               # 09-20, not the later 10-16 snapshot
    assert "AMD" not in p.names()                                # 07-01 is older than 30 days
    q = p.quantile("NVDA", 0.21)                                 # 0.02 -> 90, 0.4 -> 100: K = 95 -> -5%
    assert q is not None and q[0] == pytest.approx(-0.05, rel=1e-12)
    assert q[1]["source"] == "options" and q[1]["snapshot_date"] == "2026-09-20" and q[1]["dte"] == 30
    g = p.quantile("GOOGL", 0.01)                                # no chain: VXGOG close 30 on 10-02
    assert g is not None and g[1] == {"source": "vol_index", "index": "VXGOG", "date": "2026-10-02",
                                      "vol": pytest.approx(0.30), "q": 0.01}
    assert g[0] == pytest.approx(lognormal_quantile(0.01, 0.30, INDEX_T_YEARS), rel=1e-12)
    assert p.implied_prob("GOOGL", -0.2) == pytest.approx(lognormal_tail_prob(-0.2, 0.30, INDEX_T_YEARS))
    assert p.quantile("MU", 0.01) is None


def test_provider_without_as_of_keeps_the_latest_snapshot(tmp_path):
    _write_options(tmp_path, vxgog=None)
    p = OptionSignalProvider.load(tmp_path)
    assert p is not None and p.atm_iv("NVDA") == pytest.approx(0.50)


# --- option anchors ---------------------------------------------------------------------------- #
class _Market:
    def __init__(self, quantiles: dict[float, float] | None):
        self.q = quantiles

    def quantile(self, ticker: str, q: float):
        if self.q is None or q not in self.q:
            return None
        return self.q[q], {"source": "options", "snapshot_date": "2026-10-06", "dte": 31, "q": q}


def test_option_anchors_from_the_quantiles_with_severe_capped():
    anchors, src = option_anchors("eq:NVDA", "down", _Market({0.05: -0.14, 0.01: -0.70}), OptionSizingConfig(),
                                  CFG, BOUNDS)
    assert anchors == {"mild": -0.14, "adverse": -0.70, "severe": pytest.approx(-0.96)}   # -1.05 capped at 96%
    assert src["source"] == "options" and src["mild_q"] == 0.05 and src["adverse_move"] == -0.70


def test_option_anchors_fall_back_with_a_reason():
    sizing = OptionSizingConfig()
    for lead, market in (("eq:NVDA", None), ("ir:DGS10", _Market({})), ("eq:NVDA", _Market({0.05: -0.1})),
                         ("eq:NVDA", _Market({0.05: -0.2, 0.01: -0.1}))):    # last: |adverse| < |mild|
        anchors, src = option_anchors(lead, "down", market, sizing, CFG, BOUNDS)
        assert anchors is None and src["fallback"] is True and src["reason"]


def _cov() -> FactorCovariance:
    factors = ("eq:NVDA", "eq:AMD")
    vols = np.array([0.03, 0.03])
    corr = np.array([[1.0, 0.5], [0.5, 1.0]])
    return FactorCovariance(factors=factors, mean=np.zeros(2), cov=np.outer(vols, vols) * corr,
                            n_obs=250, window="test", cov_id="toy")


def test_anchor_tier_uses_the_option_anchors_instead_of_history():
    defn = ScenarioDefinition(name="keyman_nvda", drivers={"eq:NVDA": -0.30}, overlays={}, severity="adverse",
                              severity_scale=1.0, horizon_days=21)
    book = AnchorBook({}, {}, BOUNDS, CFG)                       # no history for NVDA at all
    src = {"source": "options", "snapshot_date": "2026-10-06"}
    ms = anchor_tier("keyman_nvda", "adverse", lead="eq:NVDA", direction="down", composed=defn, authored=defn,
                     generator=ScenarioGenerator(_cov()), book=book, cfg=CFG, state_raw=1.0, state_used=1.0,
                     anchors={"mild": -0.14, "adverse": -0.22, "severe": -0.33}, anchor_source=src)
    assert ms.vector["eq:NVDA"] == pytest.approx(-0.22, rel=1e-9)        # r = 1: exactly the option anchor
    assert ms.provenance["severity"]["anchor_source"] == src
    assert ms.provenance["severity"]["anchors"]["severe"] == pytest.approx(-0.33)


# --- report flags and concentration ------------------------------------------------------------- #
def _ms(move: float, horizon: int) -> MaterializedScenario:
    return MaterializedScenario(name="s", severity="adverse", vector={"eq:NVDA": move},
                                provenance={"horizon_days": horizon})


def test_option_tail_flag_only_when_the_market_is_more_extreme_within_the_horizon():
    sev = {"lead": "eq:NVDA", "direction": "down", "source": "history"}
    market = _Market({0.01: -0.22})
    flag = _option_tail_flag("s", sev, _ms(-0.15, 10), market, OptionSizingConfig())
    assert flag is not None and flag["flag"] == "below_option_tail" and flag["option_move"] == -0.22
    assert _option_tail_flag("s", sev, _ms(-0.30, 10), market, OptionSizingConfig()) is None    # tier more extreme
    assert _option_tail_flag("s", sev, _ms(-0.15, 60), market, OptionSizingConfig()) is None    # horizon > 21
    assert _option_tail_flag("s", sev, _ms(-0.15, 10), None, OptionSizingConfig()) is None


def test_stock_concentration_ranks_signed_notional():
    trades = [CashEquityTrade(trade_id="1", description="", currency="USD", notional=90.0, ticker="NVDA",
                              quantity=10.0),
              CashEquityTrade(trade_id="2", description="", currency="USD", notional=40.0, ticker="AMD",
                              quantity=-5.0),
              CashEquityTrade(trade_id="3", description="", currency="USD", notional=95.0, ticker="GOOGL",
                              quantity=3.0),
              CashEquityTrade(trade_id="4", description="", currency="USD", notional=200.0, ticker="XLK",
                              quantity=1.0)]
    assert stock_concentration(trades, frozenset({"XLK"})) == [{"ticker": "GOOGL", "notional": 95.0},
                                           {"ticker": "NVDA", "notional": 90.0},
                                           {"ticker": "AMD", "notional": -40.0}]
