"""signal-health wiring: the config, the per-source collectors, the report field and the runner's roll-up.

Synthetic resolutions with hand values; no ``data/``, services or pricing.
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pytest
from src.common.config.models import ConfigError
from src.scenariogen import runner
from src.scenariogen.arag.provider import AragCompanyProvider, AragResolution
from src.scenariogen.crowding import CrowdingLeg, CrowdingResolution
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.geopolitical import GeoResolution
from src.scenariogen.macro.provider import DriverResolution
from src.scenariogen.news.gate import NewsGateResult
from src.scenariogen.news.mapping import NewsLeg
from src.scenariogen.news.provider import NewsResolution
from src.scenariogen.options.provider import OptionSignalProvider, _NameSignals
from src.scenariogen.report import StressReport
from src.scenariogen.signal_health import (
    SOURCES,
    Observation,
    SignalHealthConfig,
    build_signal_health,
    crowding_observation,
    macro_observation,
    news_observation,
    options_observation,
    problem_lines,
    state_observation,
    tally,
    xbrlrag_observation,
)
from src.scenariogen.throughput import ThroughputResolution

AS_OF = date(2026, 10, 18)
LIMITS = {"macrorag": None, "xbrlrag": None, "news": 30, "crowding": 30, "options": 30, "gpr": 60,
          "throughput": None}


def _write_config(root, limits=None):
    d = root / "configs" / "scenariogen"
    d.mkdir(parents=True, exist_ok=True)
    (d / "signal_health.json").write_text(json.dumps({"description": "x", "max_staleness_days": limits or LIMITS}))


def test_committed_config_matches_the_spec_limits():
    assert SignalHealthConfig.load().max_staleness_days == LIMITS


@pytest.mark.parametrize("raw", [
    {"max_staleness_days": LIMITS, "max_stalenes": 1},                          # unknown top-level key
    {"max_staleness_days": {k: v for k, v in LIMITS.items() if k != "gpr"}},    # missing source
    {"max_staleness_days": {**LIMITS, "fx": 3}},                                # unknown source
    {"max_staleness_days": {**LIMITS, "news": -1}},
    {"max_staleness_days": {**LIMITS, "news": 2.5}},
    {"max_staleness_days": {**LIMITS, "news": True}},
    {"max_staleness_days": [1, 2]},
])
def test_invalid_config_raises(raw):
    with pytest.raises(ConfigError):
        SignalHealthConfig.from_dict(raw)


def test_build_signal_health_covers_every_source_and_ignores_dates_of_undated_sources():
    cfg = SignalHealthConfig(LIMITS)
    block = build_signal_health(AS_OF, cfg, {
        # undated: a year-old date is not reported, so not stale
        "macrorag": Observation(latest=date(2025, 10, 1), covered=3, expected=3),
        "news": Observation(latest=date(2026, 9, 1), covered=4, expected=4),        # 47 days > 30
    })
    by = {s["source"]: s for s in block["sources"]}
    assert set(by) == set(SOURCES)
    assert by["macrorag"]["latest"] is None and by["macrorag"]["status"] == "ok"
    assert by["news"]["staleness_days"] == 47 and by["news"]["status"] == "degraded"
    assert by["gpr"]["status"] == "unused"
    assert block["overall"] == "degraded" and block["counts"] == {"ok": 1, "degraded": 1, "down": 0, "unused": 5}
    with pytest.raises(ValueError, match="fx"):
        build_signal_health(AS_OF, cfg, {"fx": Observation()})


def test_tally_counts_covered_items_and_holds_the_rest_once():
    obs = tally(["a", "b", "c", "a", "d"], {"a": "", "b": "timeout", "c": None})
    assert obs == Observation(covered=1, expected=4, holds=("timeout", "not_resolved", "not_resolved"))


def test_macro_observation_reads_resolutions_and_stop_reasons():
    used = DriverResolution("yen", {}, used_macro=True)
    refused = DriverResolution("tariff", {}, fallback_reason="macro_unavailable")
    obs = macro_observation(
        ["yen", "tariff", "taiwan", "deepseek"],
        {"yen": used, "tariff": refused, "deepseek": {"used_macro": False, "fallback_reason": "macro_gate_failed"}},
        {"taiwan": "macro_not_configured"})
    assert obs == Observation(covered=1, expected=4,
                              holds=("macro_unavailable", "macro_not_configured", "macro_gate_failed"))


def test_xbrlrag_observation_counts_legs_and_stop_reasons():
    res = AragResolution("a", company_drivers={"eq:NVDA": -0.1, "eq:AMD": -0.2})
    obs = xbrlrag_observation({"a": 2, "b": 3, "c": 0}, {"a": res}, {"b": "ARag evidence GATE failed"})
    assert obs == Observation(covered=2, expected=5, holds=("ARag evidence GATE failed",))


def _news(ok: bool, n_events: int) -> NewsResolution:
    legs = {t: NewsLeg(t, 1.0, 0.0, None, 1 if i < n_events else 0) for i, t in enumerate(["NVDA", "AMD", "MU", "TSM"])}
    gate = NewsGateResult(ok, "2026-10-10", 8, n_events, "ok" if ok else "thin: 2 names with events < 3")
    return NewsResolution(as_of=AS_OF.isoformat(), gate=gate, legs=legs)


def test_news_observation_newest_filing_names_with_events_and_gate_hold():
    assert news_observation(None) == Observation()
    assert news_observation(_news(True, 3)) == Observation(latest=date(2026, 10, 10), covered=3, expected=4)
    assert news_observation(_news(False, 2)).holds == ("gate_held",)


def test_crowding_observation_newest_settlement_and_reliable_names():
    res = CrowdingResolution(
        as_of=AS_OF.isoformat(),
        legs={"NVDA": CrowdingLeg("NVDA", 1.1, 2.0, 0.8, False, False)},
        history={"NVDA": ["2026-09-15", "2026-09-30"], "AMD": ["2026-07-15"], "MU": ["2026-09-30", "2026-10-31"]})
    assert crowding_observation(None, AS_OF) == Observation()
    # floors: 2 obs on/before as_of, newest within 30 days -> NVDA only (MU has one past date, AMD is stale)
    assert crowding_observation(res, AS_OF, min_obs=2, max_staleness_days=30) == Observation(
        latest=date(2026, 9, 30), covered=1, expected=3)
    assert crowding_observation(res, AS_OF).covered == 1                    # without events.json: names with a leg


def _signals(snapshot: str | None = None, index_date: str | None = None) -> _NameSignals:
    return _NameSignals(spot=None, atm_iv=0.5, skew_25d=None, term_ratio=None, vrp=None, strikes=None, cdf=None,
                        snapshot_date=snapshot, vol_index="VXN" if index_date else None, index_date=index_date)


def test_options_observation_newest_date_and_underlier_coverage():
    provider = OptionSignalProvider({"NVDA": _signals(snapshot="2026-10-09"), "AMD": _signals(index_date="2026-10-16")})
    assert provider.data_date("NVDA") == "2026-10-09" and provider.data_date("XYZ") is None
    assert options_observation(provider, {"NVDA", "MU"}) == Observation(latest=date(2026, 10, 16), covered=1,
                                                                        expected=2)
    assert options_observation(None, {"NVDA"}) == Observation(expected=1, holds=("no_feed",))
    assert options_observation(None, set()) == Observation()


def _geo(applied: bool, day: str) -> GeoResolution:
    return GeoResolution("GPRC_TWN", AS_OF.isoformat(), day, 1.0, 0.5, 0.0, 1.0, False, 100, applied=applied)


def test_state_observation_tagged_vs_applied_with_the_series_date():
    obs = state_observation(["taiwan", "other", "third"], {"taiwan": _geo(True, "2026-09-30"),
                                                          "other": _geo(False, ""), "third": None},
                            date_attr="asof_date")
    assert obs == Observation(latest=date(2026, 9, 30), covered=1, expected=3, holds=("not_applied", "no_feed"))
    thr = ThroughputResolution(AS_OF.isoformat(), 0.1, 0.5, None, "intact", 1.0, False, 30)
    assert state_observation(["ai_deflation"], {"ai_deflation": thr}) == Observation(covered=1, expected=1)


def test_problem_lines_name_the_degraded_and_down_sources():
    block = build_signal_health(AS_OF, SignalHealthConfig(LIMITS), {
        "macrorag": Observation(covered=6, expected=10, holds=("macro_unavailable",) * 4),
        "options": Observation(latest=date(2026, 10, 9), covered=0, expected=2),
        "news": Observation(latest=date(2026, 10, 10), covered=4, expected=4)})
    assert problem_lines(block) == [
        "options DOWN: covered 0/2, 9d old, holds none",
        "macrorag DEGRADED: covered 6/10, undated, holds macro_unavailable x4",
    ]


def test_stress_report_serialises_signal_health():
    report = StressReport("2026-10-18", "run", "snap")
    assert report.to_dict()["signal_health"] is None
    report.signal_health = {"overall": "ok", "counts": {}, "sources": []}
    assert report.to_dict()["signal_health"] == {"overall": "ok", "counts": {}, "sources": []}


def _gauge(source: str) -> float:
    return runner.SCEN_SIGNAL.labels(source=source)._value.get()


def test_runner_rollup_on_the_static_path(tmp_path, caplog):
    _write_config(tmp_path)
    specs = {"taiwan": {"geopolitical": {"series": "GPRC_TWN"}}, "ai_deflation": {"throughput": {}}, "plain": {}}
    with caplog.at_level("WARNING", logger="scenariogen.runner"):
        block = runner._signal_health(
            tmp_path, AS_OF, attempted=["taiwan", "ai_deflation", "plain"], scen_specs=specs, provider=None,
            macro=False, macro_seen={}, stopped={}, geo={"taiwan": _geo(True, "2026-07-01")},
            thr={"ai_deflation": None}, market=None, trades=[], events_cfg=None)
    by = {s["source"]: s for s in block["sources"]}
    assert by["gpr"]["status"] == "degraded" and by["gpr"]["staleness_days"] == 109     # Jul 1 -> Oct 18
    assert by["throughput"]["status"] == "down" and by["throughput"]["holds"] == {"no_feed": 1}
    assert all(by[s]["status"] == "unused" for s in ("macrorag", "xbrlrag", "news", "crowding", "options"))
    assert block["overall"] == "down"
    assert (_gauge("throughput"), _gauge("gpr"), _gauge("news")) == (2, 1, -1)
    assert "signal throughput DOWN" in caplog.text and "signal gpr DEGRADED" in caplog.text


def test_runner_rollup_on_the_dual_rag_path(tmp_path):
    _write_config(tmp_path)
    arag_cfg = {"scenarios": {
        "keyman": {"companies": [{"ticker": "NVDA"}], "required_services": ["arag"]},
        "tariff": {"companies": [{"ticker": "AMD"}, {"ticker": "MU"}], "required_services": ["macro", "arag"]},
        "crowded": {"companies": [{"ticker": "SMH"}], "required_services": ["arag"]},
        "yen": {"required_services": ["macro"]}}}
    arag = AragCompanyProvider(arag_cfg, as_of=AS_OF, client=None, scenario_config={"scenarios": {}})  # type: ignore[arg-type]  # resolve() is never called
    provider = DualRagEnrichmentProvider(arag_provider=arag, scenario_config={"scenarios": {}},
                                         news_resolution=_news(True, 4))
    provider._macro_cache.update({
        "keyman": DriverResolution("keyman", {}, fallback_reason="macro_not_configured"),
        "tariff": DriverResolution("tariff", {}, fallback_reason="macro_unavailable"),
        "crowded": DriverResolution("crowded", {}, fallback_reason="macro_not_configured"),
        "yen": DriverResolution("yen", {}, used_macro=True)})
    provider._arag_cache["keyman"] = AragResolution("keyman", company_drivers={"eq:NVDA": -0.2})
    provider._arag_cache["yen"] = AragResolution("yen")
    stopped = {"tariff": "macro_unavailable", "crowded": "ARag narrative_search failed for SMH"}
    block = runner._signal_health(
        tmp_path, AS_OF, attempted=["keyman", "tariff", "crowded", "yen"], scen_specs={}, provider=provider,
        macro=False, macro_seen={}, stopped=stopped, geo={}, thr={}, market=None, trades=[], events_cfg=None)
    by = {s["source"]: s for s in block["sources"]}
    # MacroRAG: tariff and yen require it; yen applied, tariff fell back
    assert (by["macrorag"]["covered"], by["macrorag"]["expected"]) == (1, 2)
    assert by["macrorag"]["holds"] == {"macro_unavailable": 1}
    # XbrlRag: tariff never reached it (stopped for macro); keyman 1/1, crowded 0/1 stopped
    assert (by["xbrlrag"]["covered"], by["xbrlrag"]["expected"]) == (1, 2)
    assert by["xbrlrag"]["holds"] == {"ARag narrative_search failed for SMH": 1}
    assert by["news"]["status"] == "ok" and by["crowding"]["status"] == "unused"


def test_runner_rollup_on_the_macro_only_path_when_macrorag_is_down(tmp_path):
    _write_config(tmp_path)
    (tmp_path / "configs" / "scenariogen" / "macro.json").write_text(json.dumps({"scenarios": {"yen": {}}}))
    block = runner._signal_health(
        tmp_path, AS_OF, attempted=["yen", "plain"], scen_specs={}, provider=None, macro=True, macro_seen={},
        stopped={}, geo={}, thr={}, market=None, trades=[], events_cfg=None)
    macro = next(s for s in block["sources"] if s["source"] == "macrorag")
    assert macro["status"] == "down" and macro["holds"] == {"macro_unavailable": 1}
    assert np.isclose(macro["coverage"], 0.0)
