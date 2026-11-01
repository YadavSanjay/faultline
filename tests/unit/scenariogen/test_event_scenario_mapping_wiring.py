"""Unit tests for event-scenario-mapping wiring (events.json, ScenarioEvents, dual-RAG, the leg hook).

The locked spec tests cover the pure functions in ``events.py``; these cover how a run applies them:
the committed config, theme-relevant news and reliable crowding per scenario, trigger hits, the
company-leg multipliers, the non-leg equity-driver scaling in ``anchoring.leg_transform`` and the
forward-log record (fakes at the data boundary; a toy covariance; no data/, no QuantLib).
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pytest
from src.common.config.models import ConfigError
from src.scenariogen.ablation import LAYERS
from src.scenariogen.anchoring import ANCHORED_RULE, leg_transform
from src.scenariogen.config import config_dir, load_scenario_config
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.crowding import CrowdingLeg, CrowdingResolution
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.events import (
    EMPTY_THEME,
    EventsConfig,
    ScenarioEvents,
    ScenarioTheme,
    TriggerHit,
    load_events,
)
from src.scenariogen.forward_log import live_triggers
from src.scenariogen.news.gate import NewsGateResult
from src.scenariogen.news.mapping import EventRow, NewsMapConfig
from src.scenariogen.news.provider import NewsResolution
from src.scenariogen.scenario_gen import ScenarioGenerator

AS_OF = date(2026, 10, 6)
MAP = NewsMapConfig()
WEIGHTS = {"2.02": 0.6, "5.02": 1.0, "4.02": 1.0, "7.01": 0.2, "8.01": 0.2}


def _ev(age: int, items: str, acc: str) -> EventRow:
    return EventRow(filing_date=(AS_OF - timedelta(days=age)).isoformat(), item_codes=items, accession=acc,
                    weight=9.0)


def _news(events: dict[str, list[EventRow]], tone: dict[str, float | None] | None = None,
          ok: bool = True) -> NewsResolution:
    gate = NewsGateResult(ok=ok, newest_filing=AS_OF.isoformat(), staleness_days=0, n_names_with_events=3,
                          reason="" if ok else "held")
    return NewsResolution(as_of=AS_OF.isoformat(), gate=gate, pit_events=events, tone_z=tone or {}, map_cfg=MAP)


def _dates(n: int, last_age: int) -> list[str]:
    return [(AS_OF - timedelta(days=last_age + 14 * i)).isoformat() for i in range(n)][::-1]


def _crowding(mults: dict[str, float], history: dict[str, list[str]]) -> CrowdingResolution:
    legs = {t: CrowdingLeg(t, m, 3.0, 0.9, False, False) for t, m in mults.items()}
    return CrowdingResolution(as_of=AS_OF.isoformat(), legs=legs, history=history)


def _theme(news=(), tone=False, crowding=False, trig=(), names=()) -> ScenarioTheme:
    return ScenarioTheme(frozenset(news), tone, crowding, frozenset(trig), frozenset(names))


def _config(themes: dict[str, ScenarioTheme]) -> EventsConfig:
    return EventsConfig(WEIGHTS, frozenset({"XLK", "SMH"}), 24, 30, themes)


# --- committed config -------------------------------------------------------------------------- #
def test_committed_events_config_loads_and_matches_the_8k_allowlist():
    names = list(load_scenario_config().get("scenarios", {}))
    cfg = load_events(None, names)
    assert cfg is not None
    assert set(cfg.themes) == set(names)
    allowlist = json.loads((config_dir().parent / "marketdata" / "news" / "news_marketdata.json")
                           .read_text(encoding="utf-8"))["item_allowlist"]
    assert cfg.item_weights == pytest.approx({k: float(v) for k, v in allowlist.items()})
    assert cfg.themes["keyman_nvda"].trigger_items == frozenset({"5.02", "4.02"})
    assert cfg.themes["keyman_nvda"].trigger_names == frozenset({"NVDA"})
    assert cfg.themes["rate_capital"] == EMPTY_THEME and cfg.themes["inflation_repricing"] == EMPTY_THEME
    assert {"XLK", "SMH", "QQQ", "SPX", "GLD"} <= cfg.excluded


def test_load_events_is_none_without_a_file_and_rejects_a_theme_for_an_unknown_scenario(tmp_path):
    d = tmp_path / "configs" / "scenariogen"
    d.mkdir(parents=True)
    assert load_events(tmp_path, ["a"]) is None
    (d / "events.json").write_text(json.dumps({"item_weights": {"2.02": 0.6}, "crowding_min_obs": 1,
                                               "crowding_max_staleness_days": 0, "themes": {"b": {}}}))
    with pytest.raises(ConfigError, match="'b'"):
        load_events(tmp_path, ["a"])


# --- ScenarioEvents ---------------------------------------------------------------------------- #
def test_news_counts_only_the_theme_items_and_tone_only_where_the_theme_uses_it():
    news = _news({"NVDA": [_ev(0, "2.02,8.01", "a1")], "MU": [_ev(0, "8.01", "a2")]}, tone={"NVDA": -1.0})
    ev = ScenarioEvents(_config({"x": _theme(news=["2.02"]), "y": _theme(news=["2.02"], tone=True),
                                 "z": _theme()}), news, None, AS_OF)
    assert ev.news_mult("x") == pytest.approx({"NVDA": 1 + 0.15 * 0.6})          # 8.01 is not a theme item
    assert ev.news_mult("y") == pytest.approx({"NVDA": 1 + 0.15 * 0.6 + 0.10 * 1.0})
    assert ev.news_mult("z") == {}                                                 # no news theme -> 1 everywhere
    held = ScenarioEvents(_config({"x": _theme(news=["2.02"])}),
                          _news({"NVDA": [_ev(0, "2.02", "a1")]}, ok=False), None, AS_OF)
    assert held.news_mult("x") == {}                                               # held gate -> no news


def test_crowding_applies_only_to_a_crowding_theme_and_a_reliable_name():
    crowd = _crowding({"NVDA": 1.3, "AMD": 1.2, "MU": 1.0},
                      {"NVDA": _dates(30, 6), "AMD": _dates(10, 6), "MU": _dates(30, 6)})
    ev = ScenarioEvents(_config({"c": _theme(crowding=True), "n": _theme()}), None, crowd, AS_OF)
    assert ev.crowding_mult("c") == {"NVDA": 1.3}       # AMD: 10 obs < 24; MU: multiplier 1
    assert ev.crowding_mult("n") == {}
    stale = _crowding({"NVDA": 1.3}, {"NVDA": _dates(30, 45)})
    assert ScenarioEvents(_config({"c": _theme(crowding=True)}), None, stale, AS_OF).crowding_mult("c") == {}


def test_triggers_and_provenance_per_scenario():
    news = _news({"NVDA": [_ev(3, "5.02", "k1"), _ev(120, "4.02", "old")], "AMD": [_ev(1, "5.02", "k2")]})
    ev = ScenarioEvents(_config({"keyman": _theme(news=["5.02"], trig=["5.02", "4.02"], names=["NVDA"]),
                                 "other": _theme()}), news, None, AS_OF)
    assert ev.triggers("keyman") == [TriggerHit("5.02", "NVDA", (AS_OF - timedelta(days=3)).isoformat(), "k1")]
    assert ev.triggers("other") == []
    prov = ev.provenance("keyman")
    assert prov["triggers"][0]["accession"] == "k1"
    assert set(prov["news"]) == {"NVDA", "AMD"} and prov["theme"]["trigger_names"] == ["NVDA"]


# --- dual-RAG company legs --------------------------------------------------------------------- #
def test_company_leg_multipliers_follow_the_scenario_theme():
    news = _news({"NVDA": [_ev(0, "2.02", "a1")]})
    crowd = _crowding({"NVDA": 1.3}, {"NVDA": _dates(30, 6)})
    ev = ScenarioEvents(_config({"crowded": _theme(crowding=True), "earn": _theme(news=["2.02"])}),
                        news, crowd, AS_OF)
    prov = DualRagEnrichmentProvider(news_resolution=news, crowding_resolution=crowd,
                                     scenario_config={"scenarios": {}}, events=ev)
    crowded = prov._idio_parts("eq:NVDA", "crowded")
    assert set(crowded) == {"crowding"} and crowded["crowding"][0] == pytest.approx(1.3)
    assert crowded["crowding"][1]["reliable"] is True
    earn = prov._idio_parts("eq:NVDA", "earn")
    assert set(earn) == {"news"} and earn["news"][0] == pytest.approx(1 + 0.15 * 0.6)


# --- leg hook: non-leg equity drivers ---------------------------------------------------------- #
def _cov() -> FactorCovariance:
    factors = ("eq:XLK", "eq:NVDA", "eq:AMD", "eq:INTC", "credit")
    vols = np.array([0.02, 0.03, 0.03, 0.03, 0.0005])
    corr = np.full((5, 5), 0.5) + 0.5 * np.eye(5)
    return FactorCovariance(factors=factors, mean=np.zeros(5), cov=np.outer(vols, vols) * corr,
                            n_obs=250, window="test", cov_id="toy")


def _inputs(news: dict[str, float], crowd: dict[str, float]) -> dict:
    drivers = {"eq:XLK": -0.30, "eq:NVDA": -0.40, "eq:AMD": -0.35, "eq:INTC": -0.25, "credit": 0.01}
    return {"authored_drivers": dict(drivers), "authored_overlays": {}, "macro_drivers": None,
            "seed_legs": {"eq:NVDA": -0.40}, "calibrated_legs": {"eq:NVDA": -0.40},
            "news_mult": {}, "crowding_mult": {},
            "equity_events": {"news": news, "crowding": crowd, "theme": _theme(news=["2.02"], crowding=True),
                              "excluded": frozenset({"XLK"}), "lead": "eq:XLK"}}


def test_leg_hook_scales_the_company_specific_part_of_non_leg_equity_drivers():
    record: dict = {}
    t = leg_transform(ScenarioGenerator(_cov()), 1.5, record)
    out = t(_inputs({"AMD": 1.2, "NVDA": 1.4, "XLK": 1.3}, {"INTC": 1.1}), frozenset(LAYERS))
    base = _inputs({}, {})["authored_drivers"]
    others = {k: v for k, v in base.items() if k not in ("eq:AMD", "eq:NVDA")}
    s_amd = _cov().conditional_mean(others)["eq:AMD"]
    assert out["authored_drivers"]["eq:AMD"] == pytest.approx(s_amd + (-0.35 - s_amd) * 1.2, rel=1e-12)
    others = {k: v for k, v in base.items() if k not in ("eq:INTC", "eq:NVDA")}
    s_intc = _cov().conditional_mean(others)["eq:INTC"]
    assert out["authored_drivers"]["eq:INTC"] == pytest.approx(s_intc + (-0.25 - s_intc) * 1.1, rel=1e-12)
    assert out["authored_drivers"]["eq:XLK"] == -0.30                     # lead and excluded: untouched
    assert out["authored_drivers"]["eq:NVDA"] == -0.40                    # a company leg: handled as a leg
    assert set(record["scaled_drivers"]) == {"eq:AMD", "eq:INTC"}
    assert record["scaled_drivers"]["eq:INTC"]["crowding"] == pytest.approx(1.1)


def test_leg_hook_driver_scaling_follows_the_enabled_layers():
    t = leg_transform(ScenarioGenerator(_cov()), 1.5, {})
    inputs = _inputs({"AMD": 1.2}, {"INTC": 1.1})
    crowd_only = t(inputs, frozenset({"crowding"}))
    assert crowd_only["authored_drivers"]["eq:AMD"] == -0.35              # news off
    assert crowd_only["authored_drivers"]["eq:INTC"] != -0.25
    assert t(inputs, frozenset({"macrorag"})) is inputs                   # neither layer on: unchanged
    # Without the XbrlRag narrative there are no legs, so NVDA is an ordinary driver and gets its news.
    no_legs = t(_inputs({"NVDA": 1.2}, {}), frozenset({"news"}))
    assert no_legs["authored_drivers"]["eq:NVDA"] != -0.40


def test_definition_for_drops_equity_events_before_composing():
    ev = ScenarioEvents(_config({}), None, None, AS_OF)
    prov = DualRagEnrichmentProvider(scenario_config={"scenarios": {"s": {
        "drivers": {"eq:XLK": {"move_pct": -10}}, "severity": {"lead": "eq:XLK", "direction": "down"}}}},
        events=ev)
    inputs = prov.layer_inputs("s")
    assert inputs["equity_events"]["lead"] == "eq:XLK"
    defn, res = prov.definition_for("s", layers=frozenset())
    assert defn.drivers == {"eq:XLK": pytest.approx(-0.10)}
    assert res.to_dict()["events"]["triggers"] == []


# --- forward log ------------------------------------------------------------------------------- #
def test_forward_log_records_the_scenario_hits_and_the_rule_is_v3():
    hit = TriggerHit("5.02", "NVDA", "2026-10-03", "k1")
    out = live_triggers(news=_news({}), event_hits=[hit])
    assert out["news_items"] == [{"item": "5.02", "ticker": "NVDA", "filing_date": "2026-10-03", "accession": "k1"}]
    assert live_triggers(event_hits=[])["news_items"] == []
    assert ANCHORED_RULE == "dualrag-v4"
