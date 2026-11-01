"""Point-in-time event replay runner (scenario-event-replay; design §7.4, step 14b).

For each episode in ``configs/scenariogen/event_replay.json``, rebuild the scenario set as it stood on
the episode's ``as_of`` and score it against what happened next:

* the factor history, covariance and long-history anchors are cut at ``as_of``
  (``PitFactorHistory``, ``pit_long_history``); the implied-correlation regime that sets the Σ
  shrinkage reads the COR3M level on or before ``as_of``, and an issuer credit factor whose DTCC
  history starts after ``as_of`` is dropped (its backfill ratio is only known later), so a scenario's
  issuer leg falls back to ``credit``;
* scenarios whose own replay window ends on or after ``as_of`` drop out (``scenario_available``);
* each available scenario is materialized authored-only and, with ``--dual-rag``, enriched, with every
  source pinned to ``as_of``: MacroRAG ``date_to`` (macro-as-of-filter), XbrlRag fact ``as_of`` and
  filed fiscal years (``pit_arag_config``), news / crowding / GPR / throughput / events resolved as of
  ``as_of``, and option signals PIT (none exist before 2026: option-anchored scenarios fall back to
  history, flagged);
* the realised factor moves over the episode come from the full, uncut history.

Scores (``score_episode``): per scenario and version, ``forward_log.score_prediction`` (cosine, equity
rank correlation, direction hit rate) plus ``sleeve_direction_accuracy``; per version the
``nearest_scenario``. No pricing. The report goes to ``data/scenariogen/event_replay/``.

Usage::

    python -m src.scenariogen.event_replay_runner [--episode NAME] [--dual-rag] [--tier mild]
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import storage
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.provenance import RunProvenance
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.runner import _setup_logging
from src.scenariogen.ablation import LAYERS
from src.scenariogen.anchoring import (
    AnchorBook,
    SeverityConfig,
    anchor_tier,
    leg_transform,
    option_anchors,
    severity_spec,
)
from src.scenariogen.arag.client import RestTransport
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.config import (
    config_dir,
    load_arag_config,
    load_geopolitical_config,
    load_plausibility_config,
    load_throughput_config,
)
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.event_replay import (
    Episode,
    event_replay_settings,
    latest_filed_fiscal_years,
    load_event_replay_config,
    nearest_scenario,
    pit_issuer_drops,
    scenario_available,
    sleeve_direction_accuracy,
)
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.forward_log import horizon_scale, realised_moves, score_prediction
from src.scenariogen.geopolitical import GeopoliticalProvider
from src.scenariogen.long_history import load_long_history
from src.scenariogen.options import OptionSignalProvider
from src.scenariogen.options.sizing import OptionSizingConfig
from src.scenariogen.plausibility import hard_bound
from src.scenariogen.runner import _build_dual_provider, _geo_for, _throughput_for
from src.scenariogen.throughput import ThroughputProvider

logger = logging.getLogger("scenariogen.event_replay")

FILING_LAG_DAYS = 90            # a 10-K is filed within 60-90 days of the fiscal year end
NARRATIVE_YEARS = 2
VERSIONS = ("authored", "enriched")


class PitFactorHistory(FactorHistory):
    """A ``FactorHistory`` whose every factor series stops at ``cutoff`` (inclusive).

    Issuer credit factors whose DTCC history starts after the cutoff are dropped
    (``pit_issuer_drops``): their pre-DTCC backfill is scaled by a ratio measured at the first print.
    The dropped keys are kept in ``dropped_issuer_factors`` for the report.
    """

    def __init__(self, project_root: Path, config: dict[str, Any], cutoff: date):
        super().__init__(project_root, config)
        self.cutoff = pd.Timestamp(cutoff)
        self.dropped_issuer_factors: list[str] = []

    def _collect_series(self, *, include_style: bool = False) -> dict[str, pd.Series]:
        series = super()._collect_series(include_style=include_style)   # fills issuer_credit_info
        drops = pit_issuer_drops(self.issuer_credit_info, self.cutoff.date())
        self.dropped_issuer_factors = sorted(drops)
        return {k: s[pd.to_datetime(s.index) <= self.cutoff] for k, s in series.items() if k not in drops}


def pit_long_history(root: Path, cfg: SeverityConfig, cutoff: date) -> AnchorBook:
    """The long-history anchor book with every factor's moves cut at ``cutoff``."""
    series, info = load_long_history(root, min_overlap=cfg.min_overlap)
    ts = pd.Timestamp(cutoff)
    cut = {f: s[pd.to_datetime(s.index) <= ts] for f, s in series.items()}
    return AnchorBook(cut, info, load_plausibility_config(root).get("bounds", {}), cfg)


def pit_arag_config(arag_cfg: Mapping[str, Any], as_of: date,
                    fye: Callable[[str], tuple[int, int] | None]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``arag.json`` with each company leg pinned to the fiscal years filed by ``as_of``.

    Narrative legs search the last ``NARRATIVE_YEARS`` filed fiscal years; a calibration reads the last
    filed one. A leg whose filer's fiscal year-end is unknown, or with no filed year, is dropped (an
    unfiltered search would look ahead).

    Args:
        arag_cfg: The parsed ``arag.json``.
        as_of: The replay date.
        fye: Ticker -> ``(fye_month, fye_day)`` from XbrlRag, or None when unknown.

    Returns:
        ``(config, dropped)``: the pinned copy and one ``{"scenario", "ticker", "reason"}`` per dropped leg.
    """
    cfg = copy.deepcopy(dict(arag_cfg))
    dropped: list[dict[str, Any]] = []
    for name, scen in cfg.get("scenarios", {}).items():
        kept = []
        for leg in scen.get("companies", []) or []:
            ticker = str(leg.get("ticker", "")).upper()
            end = fye(ticker)
            years = (latest_filed_fiscal_years(NARRATIVE_YEARS, end[0], end[1], as_of,
                                               filing_lag_days=FILING_LAG_DAYS) if end else [])
            if not years:
                dropped.append({"scenario": name, "ticker": ticker,
                                "reason": "no fiscal year-end" if end is None else "no filed fiscal year"})
                continue
            leg["fiscal_years"] = years
            if isinstance(leg.get("calibration"), dict):
                leg["calibration"]["fiscal_year"] = years[-1]
            kept.append(leg)
        scen["companies"] = kept
        scen["expect_tickers"] = [str(c.get("ticker", "")).upper() for c in kept]
    return cfg, dropped


def score_episode(vectors: Mapping[str, Mapping[str, Mapping[str, float]]], realised: Mapping[str, float],
                  scale: Mapping[str, float], sleeves: Mapping[str, str], *,
                  min_abs_z: float) -> dict[str, Any]:
    """Score every version's scenario vectors against one episode's realised moves.

    Args:
        vectors: Version (authored | enriched) -> scenario -> materialized factor vector.
        realised: Factor -> realised move over the episode.
        scale: Factor -> horizon volatility (as-of covariance).
        sleeves: Factor -> sleeve.
        min_abs_z: Smallest realised |z| that counts towards direction accuracy.

    Returns:
        Version -> ``{"scores": scenario -> score_prediction + "sleeves", "nearest": name | None}``.
    """
    out: dict[str, Any] = {}
    for version, by_name in vectors.items():
        scores: dict[str, dict[str, Any]] = {}
        for name, vec in by_name.items():
            s = score_prediction(vec, realised, scale, min_abs_z=min_abs_z)
            s["sleeves"] = sleeve_direction_accuracy(vec, realised, scale, sleeves, min_abs_z=min_abs_z)
            scores[name] = s
        out[version] = {"scores": scores, "nearest": nearest_scenario(scores)}
    return out


def _fye_lookup(conn: Mapping[str, Any]) -> Callable[[str], tuple[int, int] | None]:
    """Ticker -> (fye_month, fye_day) from XbrlRag ``/api/v1/companies/{ticker}`` (cached)."""
    transport = RestTransport(str(conn.get("base_url", "")), timeout_s=float(conn.get("health_timeout_s", 60)),
                              max_attempts=1)
    cache: dict[str, tuple[int, int] | None] = {}

    def look(ticker: str) -> tuple[int, int] | None:
        if ticker not in cache:
            try:
                r = transport.get(f"/api/v1/companies/{ticker}")
                m, d = r.get("fye_month"), r.get("fye_day")
                cache[ticker] = (int(m), int(d)) if m and d else None
            except Exception as exc:
                logger.warning("  XbrlRag fiscal year-end for %s unavailable: %s", ticker, exc)
                cache[ticker] = None
        return cache[ticker]

    return look


def _realised_panel(full: Mapping[str, pd.Series]) -> pd.DataFrame:
    """The full factor history on the equity trading calendar (eq:XLK dates), uncut."""
    cal = pd.to_datetime(full["eq:XLK"].index)
    return pd.DataFrame({k: pd.Series(s.to_numpy(), index=pd.to_datetime(s.index)).reindex(cal)
                         for k, s in full.items()}, index=cal)


def _materialize(name: str, tier: str, catalog: ScenarioCatalog, provider: Any, *, book: AnchorBook,
                 sev_cfg: SeverityConfig, state_raw: float, market: Any, sizing: OptionSizingConfig,
                 bounds: Mapping[str, Any], replay_bounds: dict[str, float]) -> tuple[MaterializedScenario, list[str]]:
    """One tier of one scenario, anchored on the as-of book; returns it with any flags."""
    spec = catalog.spec(name)
    generator = catalog.generator_for(tier, name)
    sev = severity_spec(spec)
    flags: list[str] = []
    if sev is None:
        defn = catalog.definition(name, tier, severity_multiplier=state_raw)
        if provider is not None:
            defn, _ = provider.definition_for(name, severity=tier, severity_scale=defn.severity_scale)
        return generator.materialize(defn), flags
    anchors = source = None
    if sev["source"] == "options":
        anchors, source = option_anchors(sev["lead"], sev["direction"], market, sizing, sev_cfg, bounds)
        if anchors is None:
            flags.append("option_anchor_unavailable")
    state_used = max(state_raw, sev_cfg.state_floor)
    authored = replace(catalog.definition(name, tier), severity_scale=1.0)
    record: dict[str, Any] = {}
    if provider is not None:
        hook = leg_transform(generator, sev_cfg.leg_cap, record, lead=sev["lead"])
        composed, _ = provider.definition_for(name, severity=tier, severity_scale=1.0, state_mult=state_used,
                                              leg_transform=hook)
    else:
        composed = replace(authored, severity_scale=state_used)
    ms = anchor_tier(name, tier, lead=sev["lead"], direction=sev["direction"], composed=composed,
                     authored=authored, generator=generator, book=book, cfg=sev_cfg, state_raw=state_raw,
                     state_used=state_used, legs=record,
                     bounds=replay_bounds if name in catalog.replays else None,
                     anchors=anchors, anchor_source=source)
    return ms, flags


def replay_episode(ep: Episode, *, root: Path, config: dict[str, Any], full: Mapping[str, pd.Series],
                   sleeves: Mapping[str, str], min_abs_z: float, tier: str, dual_rag: bool) -> dict[str, Any]:
    """Rebuild, materialize and score the scenario set for one episode."""
    logger.info("Episode %s: as of %s, horizon %d", ep.name, ep.as_of, ep.horizon_days)
    history = PitFactorHistory(root, config, ep.as_of)
    catalog = ScenarioCatalog.from_config(history, project_root=root)
    sev_cfg = SeverityConfig.load(root)
    book = pit_long_history(root, sev_cfg, ep.as_of)
    bounds = dict(load_plausibility_config(root).get("bounds", {}))
    replay_bounds = {f: b for f in catalog.covariance.factors if (b := hard_bound(bounds, f)) is not None}
    sizing = OptionSizingConfig.load(root)
    market = OptionSignalProvider.load(root, None, as_of=ep.as_of, sizing=sizing)
    geo = GeopoliticalProvider.load(root, load_geopolitical_config(root))
    thr = ThroughputProvider.load(root, load_throughput_config(root))

    dropped: list[dict[str, Any]] = []
    available: list[str] = []
    for name in catalog.names():
        spec = load_scenario_spec(root, name)
        if not scenario_available(spec, ep.as_of):
            dropped.append({"scenario": name, "reason": "replay window ends on or after the as-of"})
        elif name in catalog.errors:
            dropped.append({"scenario": name, "reason": catalog.errors[name].reason})
        else:
            available.append(name)

    provider: DualRagEnrichmentProvider | None = None
    arag_dropped: list[dict[str, Any]] = []
    if dual_rag:
        arag_cfg, arag_dropped = pit_arag_config(load_arag_config(root), ep.as_of,
                                                 _fye_lookup(load_arag_config(root).get("connection", {})))
        provider = _build_dual_provider(root, ep.as_of, available, catalog.scenario_config(),
                                        arag_config=arag_cfg, arag_as_of=ep.as_of)

    vectors: dict[str, dict[str, dict[str, float]]] = {v: {} for v in VERSIONS if v == "authored" or dual_rag}
    flags: dict[str, list[str]] = {}
    for name in available:
        spec = catalog.spec(name)
        g, t = _geo_for(name, spec, geo, ep.as_of), _throughput_for(name, spec, thr, ep.as_of)
        state = (g.multiplier if g is not None else 1.0) * (t.multiplier if t is not None else 1.0)
        for version in vectors:
            prov = provider if version == "enriched" else None
            try:
                ms, f = _materialize(name, tier, catalog, prov, book=book, sev_cfg=sev_cfg, state_raw=state,
                                     market=market, sizing=sizing, bounds=bounds, replay_bounds=replay_bounds)
            except ScenarioGenerationError as exc:
                dropped.append({"scenario": name, "version": version, "reason": exc.reason, "detail": exc.detail})
                continue
            vectors[version][name] = dict(ms.vector)
            if f:
                flags.setdefault(name, []).extend(x for x in f if x not in flags.get(name, []))

    factors = sorted({k for by in vectors.values() for vec in by.values() for k in vec})
    realised = realised_moves(_realised_panel(full), ep.as_of, ep.horizon_days, factors) or {}
    scale = horizon_scale(catalog.covariance, ep.horizon_days, factors)
    scored = score_episode(vectors, realised, scale, sleeves, min_abs_z=min_abs_z)
    for version, block in scored.items():
        logger.info("  %-9s nearest=%s %s", version, block["nearest"],
                    {n: round(s["cosine"], 2) for n, s in block["scores"].items() if s["cosine"] is not None})
    return {
        "name": ep.name, "as_of": ep.as_of.isoformat(), "horizon_days": ep.horizon_days, "tier": tier,
        "available": available, "dropped": dropped, "arag_dropped_legs": arag_dropped, "flags": flags,
        "realised": realised, "versions": scored, "cov_id": catalog.covariance.cov_id,
        "layers": list(LAYERS) if dual_rag else [],
        "pit": pit_provenance(catalog, history),
    }


def pit_provenance(catalog: ScenarioCatalog, history: PitFactorHistory) -> dict[str, Any]:
    """The point-in-time inputs of one episode, for the report.

    Args:
        catalog: The episode's catalog (built from ``history``).
        history: The episode's cut factor history, after it has been read.

    Returns:
        ``{"regime_asof", "dropped_issuer_factors"}``: the date of the implied-correlation level that set
        the Σ shrinkage (None when the regime is off or had no data by the as-of) and the issuer credit
        factors dropped because their DTCC history starts after the as-of.
    """
    regime = catalog.covariance.regime
    return {"regime_asof": regime.get("asof") if regime else None,
            "dropped_issuer_factors": list(history.dropped_issuer_factors)}


def load_scenario_spec(root: Path, name: str) -> dict[str, Any]:
    """The raw ``scenarios.json`` entry (its ``replay`` block intact, before replay resolution)."""
    raw = json.loads((config_dir(root) / "scenarios.json").read_text(encoding="utf-8"))
    return dict(raw.get("scenarios", {}).get(name, {}))


def run_event_replay(*, episode: str | None = None, dual_rag: bool = False, tier: str = "mild",
                     root: Path | None = None) -> Path | str:
    """Replay every configured episode (or one) and write the report.

    Returns:
        The report location.

    Raises:
        ConfigError: An invalid ``event_replay.json``.
        ValueError: ``episode`` names no configured episode.
    """
    root = root or get_project_root()
    config = load_pricer_config()
    raw = json.loads((config_dir(root) / "event_replay.json").read_text(encoding="utf-8"))
    episodes = load_event_replay_config(raw)
    min_abs_z, sleeves = event_replay_settings(raw)
    if episode is not None:
        episodes = [e for e in episodes if e.name == episode]
        if not episodes:
            raise ValueError(f"unknown episode {episode!r}")
    run_prov = RunProvenance.capture(root, config)
    full = FactorHistory(root, config).factor_series(include_style=True)
    results = [replay_episode(ep, root=root, config=config, full=full, sleeves=sleeves, min_abs_z=min_abs_z,
                              tier=tier, dual_rag=dual_rag) for ep in episodes]
    report = {"run_id": run_prov.run_id, "generated_at": datetime.now(UTC).isoformat(),
              "provenance": run_prov.as_dict() if hasattr(run_prov, "as_dict") else {"run_id": run_prov.run_id},
              "dual_rag": dual_rag, "min_abs_z": min_abs_z, "episodes": results, "limitations": []}
    out = storage.join(storage.data_base(root), "scenariogen", "event_replay", f"event_replay_{run_prov.run_id}.json")
    storage.write_text_atomic(out, json.dumps(report, indent=2, default=str))
    logger.info("Wrote event replay report -> %s", out)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: parse the arguments, run the replay, return the exit code."""
    p = argparse.ArgumentParser(description="Point-in-time event replay of the scenario set (step 14b)")
    p.add_argument("--episode", default=None, help="one episode name; default: all")
    p.add_argument("--dual-rag", action="store_true", help="also score the enriched (all layers) version")
    p.add_argument("--tier", default="mild", help="severity tier to materialize (default mild)")
    args = p.parse_args(argv)
    _setup_logging()
    run_event_replay(episode=args.episode, dual_rag=args.dual_rag, tier=args.tier)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
