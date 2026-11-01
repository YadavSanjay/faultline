"""Unit tests for severity-anchoring wiring (scenario-severity-anchor).

The locked spec tests cover the rules in ``severity.py``; these cover how a run applies them: config,
the anchor book, the dual-RAG leg hook, anchoring a tier, the runner and ablation paths (fakes at the
service and pricing boundaries; a toy covariance; no data/, no QuantLib).
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest
from src.common.config.models import ConfigError
from src.scenariogen.ablation import LAYERS
from src.scenariogen.ablation_runner import AnchorContext, ablate_scenario
from src.scenariogen.anchoring import (
    AnchorBook,
    SeverityConfig,
    anchor_tier,
    leg_transform,
    severity_spec,
)
from src.scenariogen.arag.provider import AragResolution
from src.scenariogen.authoring import _scenarios_entry
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.macro.provider import DriverResolution
from src.scenariogen.runner import _materialize_anchored
from src.scenariogen.scenario_gen import ScenarioDefinition, ScenarioGenerator
from src.scenariogen.versioning import compute_scenario_version

CFG = SeverityConfig()

# The authored baseline these tests were written against (the pre-replay ``ai_bust`` / ``keyman_nvda``), frozen
# here so a change to the committed scenario set doesn't move them.
AUTHORED = {"scenarios": {
    "ai_bust": {"method": "conditional_covariance", "horizon_days": 10,
                "severity": {"lead": "eq:XLK", "direction": "down"},
                "drivers": {"eq:NVDA": {"move_pct": -35}, "eq:XLK": {"move_pct": -30}}, "overlays": {}},
    "keyman_nvda": {"method": "conditional_covariance", "horizon_days": 5,
                    "severity": {"lead": "eq:NVDA", "direction": "down"},
                    "drivers": {"eq:NVDA": {"move_pct": -30}}, "overlays": {"eqvol": {"move_pts": 8}}},
}}


def _cov() -> FactorCovariance:
    factors = ("eq:XLK", "eq:NVDA", "eq:AMD", "credit", "eqvol")
    vols = np.array([0.02, 0.03, 0.03, 0.0005, 0.01])
    corr = np.full((5, 5), 0.5) + 0.5 * np.eye(5)
    return FactorCovariance(factors=factors, mean=np.zeros(5), cov=np.outer(vols, vols) * corr,
                            n_obs=250, window="test", cov_id="toy")


def _gen() -> ScenarioGenerator:
    return ScenarioGenerator(_cov())


def _book(xlk_worst: float = -0.30) -> AnchorBook:
    # 2520 calm daily moves for XLK with one crash day: 10-day record = the crash, mild = a k-th worst window.
    idx = pd.bdate_range("2000-01-03", periods=2520).strftime("%Y-%m-%d")
    moves = pd.Series(np.tile([0.004, -0.004], 1260), index=idx)
    moves.iloc[1000] = xlk_worst
    info = {"eq:XLK": {"source": "XLK", "proxy": None, "beta": None, "spliced": False,
                       "start": idx[0], "own_start": idx[0], "missing": False}}
    return AnchorBook({"eq:XLK": moves}, info, {"equity": {"max_abs_pct": 96}}, CFG)


def test_config_loads_and_rejects_unknown_keys(tmp_path):
    assert SeverityConfig.load().band == pytest.approx(0.25)
    d = tmp_path / "configs" / "scenariogen"
    d.mkdir(parents=True)
    (d / "severity.json").write_text(json.dumps({"band": 0.2, "bnad": 0.3}))
    with pytest.raises(ConfigError, match="bnad"):
        SeverityConfig.load(tmp_path)


def test_severity_spec_validates():
    assert severity_spec({}) is None
    assert severity_spec({"severity": {"lead": "eq:XLK", "direction": "down"}}) == {
        "lead": "eq:XLK", "direction": "down", "source": "history"}
    assert severity_spec({"severity": {"lead": "eq:NVDA", "direction": "down", "source": "options"}})["source"] \
        == "options"
    with pytest.raises(ConfigError):
        severity_spec({"severity": {"lead": "eq:XLK", "direction": "sideways"}})
    with pytest.raises(ConfigError, match=r"history\|options"):
        severity_spec({"severity": {"lead": "eq:XLK", "direction": "down", "source": "oracle"}})


def test_anchor_book_uses_own_history_when_proxy_beta_too_large_and_caps_severe():
    idx = pd.bdate_range("2000-01-03", periods=40).strftime("%Y-%m-%d")
    s = pd.Series([-0.01] * 40, index=idx)
    info = {"eq:TSLA": {"source": "TSLA", "proxy": "^IXIC", "beta": 2.8, "spliced": True,
                        "start": idx[0], "own_start": idx[20], "missing": False}}
    book = AnchorBook({"eq:TSLA": s}, info, {"equity": {"max_abs_pct": 10}}, CFG)
    assert book.source("eq:TSLA")["own_only"] is True
    assert book.series["eq:TSLA"].index[0] == idx[20]
    a = book.anchors("eq:TSLA", 10, "down")
    assert a is not None
    assert a["severe"] == pytest.approx(-0.10, rel=1e-12)                  # 1.5 x record capped at 10%
    assert book.anchors("eq:TSLA", 100, "down") is None                   # too little history


def test_leg_transform_reseeds_authored_legs_and_caps_only_enabled_multipliers():
    record: dict = {}
    t = leg_transform(_gen(), 1.5, record)
    inputs = {"authored_drivers": {"eq:NVDA": -0.35, "eq:XLK": -0.30}, "authored_overlays": {},
              "macro_drivers": None, "seed_legs": {"eq:NVDA": -0.35}, "calibrated_legs": {"eq:NVDA": -0.43},
              "news_mult": {"eq:NVDA": 1.22}, "crowding_mult": {"eq:NVDA": 1.30}}
    full = t(inputs, frozenset(LAYERS))
    systematic = _cov().conditional_mean({"eq:XLK": -0.30})["eq:NVDA"]
    seed = -0.35 - systematic
    assert full["seed_legs"]["eq:NVDA"] == pytest.approx(seed, rel=1e-12)  # authored minus co-move
    m = (0.43 / 0.35) * 1.22 * 1.30                                        # > 1.5 -> capped to exactly 1.5
    lam = math.log(1.5) / math.log(m)
    assert full["calibrated_legs"]["eq:NVDA"] == pytest.approx(seed * (0.43 / 0.35) ** lam, rel=1e-12)
    assert full["news_mult"]["eq:NVDA"] == pytest.approx(1.22 ** lam, rel=1e-12)
    assert record["capped_legs"] == ["eq:NVDA"]
    # Narrative only: no multiplier enabled -> compound 1, nothing capped; news map untouched.
    narr = t(inputs, frozenset({"xbrlrag_narrative"}))
    assert narr["news_mult"] == inputs["news_mult"]
    assert t(inputs, frozenset({"macrorag"})) is inputs                     # no company legs in play


def _defn(drivers: dict, overlays: dict | None = None, scale: float = 1.0) -> ScenarioDefinition:
    return ScenarioDefinition(name="ai_bust", drivers=drivers, overlays=overlays or {}, severity="mild",
                              severity_scale=scale, horizon_days=10)


def test_anchor_tier_puts_the_lead_on_the_band_limited_anchor():
    book = _book()
    anchors = book.anchors("eq:XLK", 10, "down")
    assert anchors is not None
    authored = _defn({"eq:XLK": -0.30})
    composed = _defn({"eq:XLK": -0.30}, scale=1.4)                        # enrichment +40% -> clamped to +25%
    ms = anchor_tier("ai_bust", "adverse", lead="eq:XLK", direction="down", composed=composed,
                     authored=authored, generator=_gen(), book=book, cfg=CFG, state_raw=1.4, state_used=1.4)
    assert ms.vector["eq:XLK"] == pytest.approx(anchors["adverse"] * 1.25, rel=1e-9)
    sev = ms.provenance["severity"]
    assert sev["r"] == pytest.approx(1.4, rel=1e-12)
    assert sev["lead_final"] == pytest.approx(sev["target"], rel=1e-9)
    assert "eq:XLK" in sev["factor_vs_anchor"]


def test_anchor_tier_without_history_is_fail_loud():
    with pytest.raises(ScenarioGenerationError):
        anchor_tier("x", "mild", lead="eq:AMD", direction="down", composed=_defn({"eq:AMD": -0.2}),
                    authored=_defn({"eq:AMD": -0.2}), generator=_gen(), book=_book(), cfg=CFG,
                    state_raw=1.0, state_used=1.0)


class _Macro:
    def resolve(self, name: str) -> DriverResolution:
        return DriverResolution(scenario=name, drivers={"eq:XLK": -0.30, "credit": 0.004},
                                sources={"eq:XLK": {"origin": "static"}, "credit": {"origin": "macrorag"}},
                                used_macro=True, world_view_id="wv")


class _Arag:
    def resolve(self, name: str) -> AragResolution:
        return AragResolution(scenario=name, company_drivers={"eq:NVDA": -0.35},
                              sources={"eq:NVDA": {"origin": "arag", "seed_move": -0.35}}, used_arag=True)


def _catalog() -> ScenarioCatalog:
    spec = {"drivers": {"eq:NVDA": {"move_pct": -35}, "eq:XLK": {"move_pct": -30}}, "overlays": {},
            "method": "conditional_covariance", "horizon_days": 10,
            "severity": {"lead": "eq:XLK", "direction": "down"}}
    return ScenarioCatalog(covariance=_cov(), specs={"ai_bust": spec},
                           severity_tiers={"mild": 1.0, "adverse": 1.5, "severe": 2.0})


def test_runner_anchored_dual_rag_path_without_double_count():
    book = _book()
    prov = DualRagEnrichmentProvider(scenario_config=AUTHORED, macro_provider=_Macro(), arag_provider=_Arag())
    ms = _materialize_anchored("ai_bust", "mild", _catalog(), _gen(), prov, prov_key="dualrag",
                               state_raw=0.85, sev={"lead": "eq:XLK", "direction": "down"}, book=book, cfg=CFG)
    sev = ms.provenance["severity"]
    assert sev["state_used"] == 1.0 and sev["state_raw"] == 0.85         # floored: calm data can't damp it
    assert ms.vector["eq:XLK"] == pytest.approx(sev["target"], rel=1e-9)
    assert "dualrag" in ms.provenance
    # NVDA = k x (systematic + company-specific): the authored -35% is not double counted.
    k = sev["k"]
    pre = ms.vector["eq:NVDA"] / k
    assert pre == pytest.approx(-0.35, rel=0.05)


def test_ablation_with_anchoring_keeps_every_subset_inside_the_band():
    book = _book()
    prov = DualRagEnrichmentProvider(scenario_config=AUTHORED, macro_provider=_Macro(), arag_provider=_Arag())

    def price(ms):
        return {"T_XLK": 100e6 * ms.vector.get("eq:XLK", 0.0), "T_NVDA": 50e6 * ms.vector.get("eq:NVDA", 0.0)}

    ctx = AnchorContext("eq:XLK", "down", book, CFG)
    rec = ablate_scenario("ai_bust", "mild", provider=prov, generator=_gen(), price=price, tier_scale=1.0,
                          state_mult=1.2, anchoring=ctx)
    anchor = book.anchors("eq:XLK", 10, "down")["mild"]
    assert anchor < 0
    for sub in rec["subsets"]:
        lead = sub["vector"]["eq:XLK"]
        assert anchor * 1.25 - 1e-9 <= lead <= anchor * 0.75 + 1e-9      # within the band (negative anchor)
    assert rec["composition_rule"] == "dualrag-v4"
    assert sum(rec["shapley"].values()) == pytest.approx(
        rec["ladder"][-1]["total_pnl"] - rec["ladder"][0]["total_pnl"], rel=1e-9)


def test_severity_block_changes_the_version_and_survives_authoring():
    spec = {"drivers": {"eq:XLK": {"move_pct": -30}}, "overlays": {}, "method": "conditional_covariance",
            "horizon_days": 10}
    anchored = dict(spec, severity={"lead": "eq:XLK", "direction": "down"})
    assert compute_scenario_version(spec) != compute_scenario_version(anchored)
    entry = _scenarios_entry(dict(anchored, geopolitical={"series": "GPRC_TWN"}, throughput={}))
    assert entry["severity"] == {"lead": "eq:XLK", "direction": "down"}
    assert entry["geopolitical"] == {"series": "GPRC_TWN"} and entry["throughput"] == {}
