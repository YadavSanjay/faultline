"""Severity anchoring wiring: history anchors applied to composed scenario tiers.

``severity.py`` holds the locked rules (anchors, leg cap, company-specific seed, anchor scale). This
module applies them to a run:

* ``SeverityConfig`` reads ``configs/scenariogen/severity.json``;
* ``AnchorBook`` computes each factor's tier anchors from the long history (``long_history``), using
  own moves only where a proxy splice needed a beta above ``max_proxy_beta``, and the plausibility
  bound as the severe cap;
* ``leg_transform`` is the dual-RAG hook (``DualRagEnrichmentProvider.definition_for``) that reseeds
  company legs with their company-specific part and caps their stacked multipliers, then scales the
  company-specific part of every other single-name equity driver by its theme-relevant news x crowding
  multiplier (``events.scale_driver_residuals``), per layer subset;
* ``anchor_tier`` rescales a composed tier so its lead factor sits on the tier's anchor (within the
  band), materializes it and stamps a ``severity`` block into the provenance.

A scenario is anchored when its ``scenarios.json`` entry carries ``"severity": {"lead", "direction"}``.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.config.models import ConfigError

from . import factor_keys as fk
from .config import config_dir, load_plausibility_config
from .errors import ScenarioGenerationError
from .events import equity_leg_multipliers, scale_driver_residuals
from .factor_vector import MaterializedScenario
from .long_history import load_long_history
from .options.sizing import OptionSizingConfig, option_tiers
from .plausibility import hard_bound
from .scenario_gen import ScenarioDefinition, ScenarioGenerator
from .severity import affine_scale_cap, anchor_scale, cap_leg_inputs, lead_systematic, residual_seeds, tier_anchors

logger = logging.getLogger("scenariogen.anchoring")

ANCHORED_RULE = "dualrag-v4"
LegTransform = Callable[[dict[str, Any], frozenset[str]], dict[str, Any]]


@dataclass(frozen=True)
class SeverityConfig:
    """``configs/scenariogen/severity.json`` (a mistyped key raises ``ConfigError``)."""

    mild_return_years: float = 10.0
    severe_factor: float = 1.5
    band: float = 0.25
    leg_cap: float = 1.5
    state_floor: float = 1.0
    max_proxy_beta: float = 1.5
    min_overlap: int = 250

    @classmethod
    def load(cls, root: Path | None = None) -> SeverityConfig:
        raw = json.loads((config_dir(root) / "severity.json").read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if not k.startswith(("_", "description"))}
        unknown = set(values) - known
        if unknown:
            raise ConfigError(f"severity.json: unknown key(s) {sorted(unknown)}; expected {sorted(known)}")
        return cls(**values)


def severity_spec(spec: Mapping[str, Any]) -> dict[str, str] | None:
    """The scenario's ``severity`` block, or None when unanchored.

    Returns ``{"lead", "direction", "source"}``; ``source`` is ``"history"`` (the default: long-history
    anchors) or ``"options"`` (the lead's option-implied tiers, ``option_anchors``).

    Raises:
        ConfigError: A block without a lead, with a direction other than up|down, or an unknown source.
    """
    sev = spec.get("severity")
    if not isinstance(sev, dict):
        return None
    if sev.get("direction") not in ("up", "down") or not sev.get("lead"):
        raise ConfigError(f"scenario severity block needs a lead and direction up|down, got {sev}")
    source = str(sev.get("source", "history"))
    if source not in ("history", "options"):
        raise ConfigError(f"scenario severity source must be history|options, got {source!r}")
    return {"lead": str(sev["lead"]), "direction": str(sev["direction"]), "source": source}


def option_anchors(lead: str, direction: str, market: Any, sizing: OptionSizingConfig,
                   cfg: SeverityConfig, bounds: Mapping[str, Any]) -> tuple[dict[str, float] | None, dict[str, Any]]:
    """The lead's option-implied tier anchors for a ``"source": "options"`` scenario (options-shock-sizing).

    mild / adverse are the ``mild_q`` / ``adverse_q`` quantiles of the PIT option-implied distribution
    (upper tail for ``direction == "up"``); severe = adverse x ``severe_factor``, capped at the hard bound.

    Args:
        lead: The lead factor (an ``eq:`` factor; anything else has no option data).
        direction: ``"up"`` or ``"down"``.
        market: The PIT ``OptionSignalProvider``, or None.
        sizing: The quantiles.
        cfg: Severity config (``severe_factor``).
        bounds: ``plausibility.json`` bounds.

    Returns:
        ``(anchors, source)``: anchors None when no option data is usable; ``source`` says where the
        anchors came from or why they fell back to history.
    """
    if market is None or not fk.is_equity(lead):
        return None, {"source": "history", "fallback": True, "reason": "no option data for the lead"}
    ticker = fk.equity_name(lead)
    qm, qa = ((sizing.mild_q, sizing.adverse_q) if direction == "down"
              else (1.0 - sizing.mild_q, 1.0 - sizing.adverse_q))
    mild, adverse = market.quantile(ticker, qm), market.quantile(ticker, qa)
    if mild is None or adverse is None:
        return None, {"source": "history", "fallback": True,
                      "reason": f"no PIT option quantile for {ticker} within the staleness limit"}
    try:
        anchors = option_tiers(mild[0], adverse[0], severe_factor=cfg.severe_factor,
                               bound=hard_bound(dict(bounds), lead))
    except ValueError as exc:
        return None, {"source": "history", "fallback": True, "reason": f"option quantiles unusable: {exc}"}
    return anchors, dict(adverse[1], mild_q=qm, adverse_q=qa, mild_move=mild[0], adverse_move=adverse[0])


class AnchorBook:
    """Tier anchors per factor from the long history, cached per (factor, horizon, direction)."""

    def __init__(self, series: Mapping[str, pd.Series], info: Mapping[str, dict[str, Any]],
                 bounds: Mapping[str, Any], cfg: SeverityConfig):
        self.cfg = cfg
        self.bounds = bounds
        self.info: dict[str, dict[str, Any]] = {}
        self.series: dict[str, pd.Series] = {}
        for f, s in series.items():
            i = dict(info.get(f, {}))
            beta = i.get("beta")
            if i.get("spliced") and i.get("own_start") and beta is not None and beta > cfg.max_proxy_beta:
                s = s[s.index >= i["own_start"]]          # proxy too amplified: own history only
                i["own_only"] = True
                i["start"] = str(s.index[0]) if len(s) else None
            self.series[f] = s
            self.info[f] = i
        self._cache: dict[tuple[str, int, str], dict[str, float] | None] = {}

    @classmethod
    def load(cls, root: Path, cfg: SeverityConfig) -> AnchorBook:
        series, info = load_long_history(root, min_overlap=cfg.min_overlap)
        return cls(series, info, load_plausibility_config(root).get("bounds", {}), cfg)

    def anchors(self, factor: str, horizon_days: int, direction: str) -> dict[str, float] | None:
        """Mild / adverse / severe anchors, or None when the factor has too little history."""
        key = (factor, horizon_days, direction)
        if key not in self._cache:
            s = self.series.get(factor)
            if s is None or len(s) < max(horizon_days, 2):
                self._cache[key] = None
            else:
                self._cache[key] = tier_anchors(
                    s, factor, horizon_days, direction, mild_return_years=self.cfg.mild_return_years,
                    severe_factor=self.cfg.severe_factor, bound=hard_bound(dict(self.bounds), factor))
        return self._cache[key]

    def source(self, factor: str) -> dict[str, Any]:
        i = self.info.get(factor, {})
        return {k: i.get(k) for k in ("source", "proxy", "beta", "spliced", "start", "own_start", "own_only",
                                      "matched")}


def leg_transform(generator: ScenarioGenerator, cap: float, record: dict[str, Any], *,
                  lead: str | None = None) -> LegTransform:
    """The dual-RAG company-leg hook: company-specific seed, then the multiplier cap.

    Only the multipliers the enabled layers apply take part in the cap, so a layer subset is capped on
    exactly the compound it uses. ``record`` collects the reseeded and capped legs for the report.

    With ``lead`` (the scenario's severity lead), a single name's systematic part is its one-factor beta
    to the lead (``severity.lead_systematic``, lead-beta-systematic); without one, the conditional mean
    on every other driver (the pre-dualrag-v4 rule).
    """
    cov = generator.cov

    def transform(inputs: dict[str, Any], layers: frozenset[str]) -> dict[str, Any]:
        return _scale_equity_drivers(_company_legs(inputs, layers), layers)

    def _lead_systematic(base: Mapping[str, float], targets: list[str]) -> dict[str, float]:
        """One-factor systematic moves of ``targets`` from the lead's move in ``base`` ({} without it)."""
        if lead is None or lead not in base or lead not in cov.factors:
            record["systematic"] = {"rule": "lead_beta", "lead": lead, "lead_move": None}
            return {}
        record["systematic"] = {"rule": "lead_beta", "lead": lead, "lead_move": float(base[lead])}
        return lead_systematic(cov, lead, float(base[lead]), targets)

    def _company_legs(inputs: dict[str, Any], layers: frozenset[str]) -> dict[str, Any]:
        legs: dict[str, float] = inputs["seed_legs"]
        if "xbrlrag_narrative" not in layers or not legs:
            return inputs
        base = (inputs["macro_drivers"] if "macrorag" in layers and inputs["macro_drivers"] is not None
                else inputs["authored_drivers"])
        anchor_drivers = {k: v for k, v in base.items() if k not in legs and k in cov.factors}
        propagated = cov.conditional_mean(anchor_drivers) if anchor_drivers else {}
        corrections: dict[str, float] = {}
        if lead is not None:
            systematic = _lead_systematic(base, list(legs))
            # A leg's final move is the propagation of the other drivers plus its overlay; the correction
            # swaps that propagation for the one-factor systematic part (lead-beta-systematic).
            if anchor_drivers:
                corrections = {f: float(systematic.get(f, 0.0)) - float(propagated.get(f, 0.0)) for f in legs}
        else:
            systematic = propagated
        seeds, cal = residual_seeds(legs, inputs["calibrated_legs"], inputs["authored_drivers"], systematic)
        facts = "xbrlrag_facts" in layers
        news = dict(inputs["news_mult"]) if "news" in layers else {}
        crowd = dict(inputs["crowding_mult"]) if "crowding" in layers else {}
        cal_used = cal if facts else dict(seeds)
        cal2, news2, crowd2 = cap_leg_inputs(seeds, cal_used, news, crowd, cap=cap)
        record["residual_legs"] = {f: {"authored": inputs["authored_drivers"][f], "systematic": systematic.get(f, 0.0),
                                       "seed": seeds[f]} for f in legs if f in inputs["authored_drivers"]}
        record["capped_legs"] = sorted(f for f in legs if not math.isclose(cal2[f], cal_used[f], rel_tol=1e-12)
                                       or news2.get(f) != news.get(f) or crowd2.get(f) != crowd.get(f))
        out = dict(inputs, seed_legs=seeds, calibrated_legs=cal2 if facts else cal)
        if corrections:
            out["leg_corrections"] = corrections
        if "news" in layers:
            out["news_mult"] = news2
        if "crowding" in layers:
            out["crowding_mult"] = crowd2
        return out

    def _scale_equity_drivers(inputs: dict[str, Any], layers: frozenset[str]) -> dict[str, Any]:
        """News x crowding on the single-name equity drivers that are not company legs (dualrag-v3+)."""
        ev: dict[str, Any] | None = inputs.get("equity_events")
        if ev is None:
            return inputs
        news = ev["news"] if "news" in layers else {}
        crowd = ev["crowding"] if "crowding" in layers else {}
        if not news and not crowd:
            return inputs
        legs = set(inputs["seed_legs"]) if "xbrlrag_narrative" in layers else set()
        use_macro = "macrorag" in layers and inputs["macro_drivers"] is not None
        out = dict(inputs)
        for key in ("authored_drivers", "macro_drivers"):
            base: dict[str, float] | None = inputs[key]
            if base is None:
                continue
            mults = equity_leg_multipliers(base, legs, news, crowd, ev["theme"], ev["excluded"], ev["lead"])
            if not mults:
                continue
            systematic: dict[str, float] = {}
            if lead is not None:
                beta = _lead_systematic(base, list(mults))
                systematic = {f: float(beta.get(f, 0.0)) for f in mults}
            else:
                for f in mults:
                    others = {k: v for k, v in base.items() if k != f and k not in legs and k in cov.factors}
                    systematic[f] = float(cov.conditional_mean(others).get(f, 0.0)) if others else 0.0
            out[key] = scale_driver_residuals(base, systematic, mults, cap=cap)
            if key == ("macro_drivers" if use_macro else "authored_drivers"):
                record["scaled_drivers"] = {
                    f: {"driver": base[f], "systematic": systematic[f], "multiplier": mults[f],
                        "news": news.get(fk.equity_name(f).upper(), 1.0),
                        "crowding": crowd.get(fk.equity_name(f).upper(), 1.0),
                        "scaled": out[key][f]} for f in mults}
        return out

    return transform



def _scaled(defn: ScenarioDefinition, k: float) -> ScenarioDefinition:
    return replace(defn, severity_scale=defn.severity_scale * k,
                   overlays={f: v * k for f, v in defn.overlays.items()})


def anchor_tier(
    name: str,
    tier: str,
    *,
    lead: str,
    direction: str,
    composed: ScenarioDefinition,
    authored: ScenarioDefinition,
    generator: ScenarioGenerator,
    book: AnchorBook,
    cfg: SeverityConfig,
    state_raw: float,
    state_used: float,
    legs: Mapping[str, Any] | None = None,
    bounds: Mapping[str, float] | None = None,
    anchors: Mapping[str, float] | None = None,
    anchor_source: Mapping[str, Any] | None = None,
) -> MaterializedScenario:
    """Rescale a composed tier onto its lead factor's anchor and materialize it.

    With ``bounds`` (a historical replay), the scale is also capped at the largest one that keeps every
    bounded factor inside its hard bound (``severity.affine_scale_cap``), so a replay's higher tiers are
    the episode as large as stays plausible.

    Args:
        name: Scenario id.
        tier: Tier label (mild | adverse | severe).
        lead: The lead factor key.
        direction: ``"up"`` or ``"down"``.
        composed: The composed definition at tier scale 1 (state already floored).
        authored: The authored-only definition at tier scale 1 (no layers, no state).
        generator: Materializes definitions.
        book: Long-history anchors.
        cfg: Severity config.
        state_raw: The state multiplier before the floor.
        state_used: The state multiplier after the floor.
        legs: The leg-transform record (reseeded / capped legs), if any.
        bounds: Factor -> hard bound (canonical units) for a replay's scale cap; None for no cap.
        anchors: Tier anchors that replace the long-history ones (an option-anchored scenario).
        anchor_source: Where ``anchors`` came from, or why an option-anchored scenario fell back.

    Returns:
        The materialized tier, with ``provenance["severity"]`` describing the anchoring.

    Raises:
        ScenarioGenerationError: No long history for the lead factor, or the lead doesn't move.
    """
    horizon = composed.horizon_days
    if anchors is None:
        anchors = book.anchors(lead, horizon, direction)
        source = dict(book.source(lead), **(anchor_source or {}))
    else:
        source = dict(anchor_source or {})
    if anchors is None:
        raise ScenarioGenerationError(name, "no_anchor", f"no long history for lead factor {lead}")
    lead_c = generator.materialize(composed).vector.get(lead, 0.0)
    lead_a = generator.materialize(authored).vector.get(lead, 0.0)
    if lead_c == 0.0:
        raise ScenarioGenerationError(name, "lead_flat", f"composed scenario doesn't move its lead {lead}")
    anchor = anchors[tier]
    k = anchor_scale(lead_c, lead_a, anchor, band=cfg.band)
    r = lead_c / lead_a if lead_a != 0 else 1.0
    target = anchor * min(max(r, 1.0 - cfg.band), 1.0 + cfg.band)
    final = _scaled(composed, k)
    ms = generator.materialize(final)
    got = ms.vector.get(lead, 0.0)
    if got != 0.0 and abs(got - target) > 1e-9 * max(abs(target), 1e-12):
        k *= target / got                         # one correction: the conditional mean has a mean term
        final = _scaled(composed, k)
        ms = generator.materialize(final)
        got = ms.vector.get(lead, 0.0)
    k_anchor, k_cap, binding = k, None, None
    if bounds is not None:
        # _scaled scales drivers and overlays, but the conditional mean has an intercept, so the tier
        # vector is affine in k: v(k) = v(0) + k (v(1) - v(0)) (replay-cap-intercept).
        at_zero = generator.materialize(_scaled(composed, 0.0)).vector
        unit = generator.materialize(_scaled(composed, 1.0)).vector
        bounded = {f: b for f, b in bounds.items() if f in unit}
        cap = affine_scale_cap(at_zero, unit, bounded)
        if math.isfinite(cap):
            k_cap = cap
            slope = {f: unit[f] - at_zero.get(f, 0.0) for f in bounded}
            binding = min((f for f in bounded if abs(slope[f]) > 0.0),
                          key=lambda f: (bounded[f] - math.copysign(1.0, slope[f]) * at_zero.get(f, 0.0))
                          / abs(slope[f]))
            if k > cap:
                k = cap
                final = _scaled(composed, k)
                ms = generator.materialize(final)
                got = ms.vector.get(lead, 0.0)
    vs_anchor: dict[str, dict[str, float]] = {}
    for f, move in ms.vector.items():
        a = book.anchors(f, horizon, "down" if move < 0 else "up") if move != 0 else None
        if a and a["mild"] != 0 and a["adverse"] != 0:
            vs_anchor[f] = {"vs_mild": move / a["mild"], "vs_adverse": move / a["adverse"]}
    ms.provenance["severity"] = {
        "rule": ANCHORED_RULE, "lead": lead, "direction": direction, "horizon_days": horizon,
        "anchors": dict(anchors), "anchor_source": source, "tier_anchor": anchor,
        "lead_composed": lead_c, "lead_authored": lead_a, "r": r, "band": cfg.band, "target": target,
        "k": k, "k_anchor": k_anchor, "k_cap": k_cap, "cap_binding": binding if k_cap is not None and k < k_anchor
        else None, "lead_final": got, "state_raw": state_raw, "state_used": state_used,
        "residual_legs": dict((legs or {}).get("residual_legs", {})),
        "capped_legs": list((legs or {}).get("capped_legs", [])),
        "scaled_drivers": dict((legs or {}).get("scaled_drivers", {})), "factor_vs_anchor": vs_anchor,
    }
    logger.info("  %-22s [%-7s] anchored %s %s: anchor=%.4f r=%.3f -> lead %.4f (k=%.3f)",
                name, tier, lead, direction, anchor, r, got, k)
    return ms
