"""crisis-sigma-tiers wiring: severe-tier crisis Σ in the catalog, the capped regime, and the replay scale cap.

Synthetic covariances, panels and anchors; no ``data/``, services or QuantLib.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.scenariogen.anchoring import AnchorBook, SeverityConfig, anchor_tier
from src.scenariogen.catalog import CRISIS_UNAVAILABLE, ScenarioCatalog
from src.scenariogen.covariance import FactorCovariance, crisis_covariance
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.implied_corr import ImpliedCorrelationRegime
from src.scenariogen.scenario_gen import ScenarioDefinition, ScenarioGenerator

CFG = SeverityConfig()
FACTORS = ("eq:XLK", "eq:NVDA", "credit")


def _cov(scale: float, cov_id: str) -> FactorCovariance:
    vols = np.array([0.02, 0.03, 0.0005]) * scale
    corr = np.full((3, 3), 0.5) + 0.5 * np.eye(3)
    return FactorCovariance(factors=FACTORS, mean=np.zeros(3), cov=np.outer(vols, vols) * corr, n_obs=250,
                            window="test", cov_id=cov_id)


def _catalog(*, crisis: FactorCovariance | None, error: str | None = None) -> ScenarioCatalog:
    spec = {"drivers": {"eq:XLK": {"move_pct": -10}}, "method": "conditional_covariance", "horizon_days": 10}
    return ScenarioCatalog(covariance=_cov(1.0, "calm"), specs={"s": spec},
                           severity_tiers={"mild": 1.0, "adverse": 1.5, "severe": 2.0},
                           crisis=crisis, crisis_coverage={"2022-01-01..2022-12-31": 142} if crisis else {},
                           crisis_error=error)


def test_severe_tier_materializes_with_crisis_sigma_and_stamps_it():
    crisis = _cov(2.0, "crisis")
    cat = _catalog(crisis=crisis)
    assert cat.covariance_for("mild") is cat.covariance and cat.covariance_for("adverse") is cat.covariance
    assert cat.covariance_for("severe") is crisis
    severe = cat.materialize("s", "severe")
    assert severe.provenance["covariance"]["cov_id"] == "crisis"
    assert severe.provenance["crisis_coverage"] == {"2022-01-01..2022-12-31": 142}
    mild = cat.materialize("s", "mild")
    assert mild.provenance["covariance"]["cov_id"] == "calm" and "crisis_coverage" not in mild.provenance


def test_no_severe_config_keeps_production_sigma_and_an_unbuildable_one_fails_loud():
    assert _catalog(crisis=None).covariance_for("severe").cov_id == "calm"
    cat = _catalog(crisis=None, error="crisis windows select 0 aligned day(s)")
    with pytest.raises(ScenarioGenerationError) as ei:
        cat.materialize("s", "severe")
    assert ei.value.reason == CRISIS_UNAVAILABLE and ei.value.scenario == "s"
    assert cat.materialize("s", "mild").severity == "mild"                 # the other tiers still run


def test_crisis_covariance_on_an_aligned_panel():
    idx = pd.bdate_range("2021-12-20", periods=40).strftime("%Y-%m-%d")
    rng = np.random.default_rng(7)
    panel = pd.DataFrame(rng.normal(0.0, 0.01, (40, 3)), index=idx, columns=list(FACTORS))
    cov, coverage = crisis_covariance(panel, [("2022-01-01", "2022-12-31"), ("2018-10-01", "2018-12-31")],
                                      shrinkage=0.2, min_days=20)
    assert coverage == {"2022-01-01..2022-12-31": 30, "2018-10-01..2018-12-31": 0}
    assert cov.n_obs == 30 and cov.factors == FACTORS


def test_regime_resolves_capped_shrinkage_and_records_the_cap():
    series = pd.Series(np.arange(1.0, 101.0), index=pd.bdate_range("2026-01-01", periods=100))
    series.iloc[-1] = 0.5                                                   # today at the bottom: idiosyncratic
    base = {"source": "COR3M", "series": series, "shrink_systemic": 0.4, "shrink_idiosyncratic": 0.65,
            "systemic_pct": 0.66, "idiosyncratic_pct": 0.33}
    uncapped = ImpliedCorrelationRegime(**base).resolve()
    capped = ImpliedCorrelationRegime(**base, max_shrinkage=0.3).resolve()
    assert uncapped.shrinkage == pytest.approx(0.65 - 0.01 * 0.25)          # percentile 1/100
    assert capped.shrinkage == pytest.approx(0.3) and capped.as_dict()["max_shrinkage"] == 0.3
    assert uncapped.as_dict()["max_shrinkage"] == 1.0


def _book() -> AnchorBook:
    idx = pd.bdate_range("2000-01-03", periods=2520).strftime("%Y-%m-%d")
    moves = pd.Series(np.tile([0.004, -0.004], 1260), index=idx)
    moves.iloc[1000] = -0.30
    info = {"eq:XLK": {"source": "XLK", "proxy": None, "beta": None, "spliced": False,
                       "start": idx[0], "own_start": idx[0], "missing": False}}
    return AnchorBook({"eq:XLK": moves}, info, {"equity": {"max_abs_pct": 96}}, CFG)


def _defn(scale: float = 1.0) -> ScenarioDefinition:
    return ScenarioDefinition(name="rp", drivers={"eq:XLK": -0.30, "credit": 0.03}, overlays={},
                              severity="severe", severity_scale=scale, horizon_days=10)


def test_replay_scale_is_capped_at_the_binding_bound():
    book, gen = _book(), ScenarioGenerator(_cov(1.0, "calm"))
    free = anchor_tier("rp", "severe", lead="eq:XLK", direction="down", composed=_defn(), authored=_defn(),
                       generator=gen, book=book, cfg=CFG, state_raw=1.0, state_used=1.0)
    k_free = free.provenance["severity"]["k"]
    assert k_free > 0.04 / 0.03                                             # the anchor alone would breach
    capped = anchor_tier("rp", "severe", lead="eq:XLK", direction="down", composed=_defn(), authored=_defn(),
                         generator=gen, book=book, cfg=CFG, state_raw=1.0, state_used=1.0,
                         bounds={"credit": 0.04, "eq:XLK": 0.96})
    sev = capped.provenance["severity"]
    # the cap keeps a 1e-9 relative margin (replay-cap-intercept)
    assert sev["k"] == pytest.approx(0.04 / 0.03, rel=1e-8) and sev["k_anchor"] == pytest.approx(k_free)
    assert sev["cap_binding"] == "credit"
    assert capped.vector["credit"] == pytest.approx(0.04, rel=1e-8)        # on its bound, not past it
    assert capped.vector["credit"] < 0.04
    assert free.provenance["severity"]["k_cap"] is None                     # no bounds -> no cap recorded


def test_tier_order_flags_mark_severe_below_adverse():
    from src.pricer.risk.scenarios import ScenarioPnl
    from src.scenariogen.report import StressReport, tier_order_flags
    from src.scenariogen.stress import ScenarioStressResult

    def res(name: str, tier: str, pnl: float) -> ScenarioStressResult:
        row = ScenarioPnl(trade_id="T1", asset_class="equity", currency="USD", base_pv=0.0, scenario_pv=pnl)
        return ScenarioStressResult(scenario=name, severity=tier, valuation_date="2026-10-06", results=[row])

    results = [res("a", "adverse", -118.9), res("a", "severe", -95.9),      # severe loses less: flagged
               res("b", "adverse", -60.0), res("b", "severe", -90.0),       # monotone: not flagged
               res("c", "adverse", -10.0)]                                  # no severe tier: not flagged
    flags = tier_order_flags(results)
    assert flags == [{"scenario": "a", "flag": "severe_below_adverse", "adverse_pnl": -118.9,
                      "severe_pnl": -95.9}]
    rep = StressReport("2026-10-06", "r", "s", results=results, tier_flags=flags)
    assert rep.to_dict()["tier_flags"] == flags
