"""DualRagEnrichmentProvider: assimilate MacroRAG + ARag into one scenario (dual-RAG P1).

Combines the two services under the user-selected **additive systematic + idiosyncratic**
model:

  * **MacroRAG** (+ any authored systematic drivers, e.g. ``eq:XLK``) sets the scenario's
    *drivers*. The conditional-covariance engine propagates them, giving every equity name its
    **systematic** co-move.
  * **ARag** sets each covered name's **idiosyncratic** company shock, added as an *overlay* on
    top of that systematic move. So ``final(eq:NVDA) = systematic(macro) + idiosyncratic(ARag)``
    — no weighting parameter; the two are orthogonal components (design decision, 2026-07-13).

Mechanics: when a *systematic anchor remains*, a name ARag shocks is *removed from the drivers*
(its systematic move now comes from propagation of the remaining drivers) and *added to the
overlays*. Because ``materialize`` does not severity-scale overlays but does scale drivers, the
ARag overlay is pre-scaled here by ``severity_scale`` so a name's idiosyncratic part tracks the
tier too (``seed * fact_multiplier * severity_scale``). For a genuinely *idiosyncratic-only*
scenario (e.g. key-man) where the ARag names are the only drivers, there is no systematic part
to add to, so the ARag shock IS the driver (left unscaled; ``materialize`` then applies the tier
multiplier and propagates the co-move to peers) — the additive split degenerates gracefully.

ARag/Macro are resolved **once per scenario** and cached across the severity tiers (the ARag
``narrative_search``/``facts_lookup`` calls are expensive). ``arag.resolve`` may raise
``ScenarioGenerationError`` (fail-loud per scenario), and so does a scenario whose ``arag.json``
``required_services`` lists "macro" when its macro legs were not applied; either propagates to
the runner, which logs/documents and skips just that scenario.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import factor_keys as fk
from .ablation import LAYERS, compose_definition
from .arag.provider import AragCompanyProvider, AragResolution
from .config import load_scenario_config
from .crowding import CrowdingResolution
from .errors import ScenarioGenerationError
from .events import ScenarioEvents
from .macro.provider import (
    FALLBACK_NOT_CONFIGURED,
    FALLBACK_UNAVAILABLE,
    DriverResolution,
    MacroScenarioProvider,
)
from .news.provider import NewsResolution
from .scenario_gen import METHOD_CONDITIONAL, ScenarioDefinition, to_canonical
from .versioning import compute_scenario_version

logger = logging.getLogger("scenariogen.dualrag")


@dataclass
class DualResolution:
    """The combined provenance for one scenario: what each service contributed."""

    scenario: str
    severity: str
    severity_scale: float
    driver_sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    overlay_sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    macro: DriverResolution | None = None
    arag: AragResolution | None = None
    news: NewsResolution | None = None
    crowding: CrowdingResolution | None = None
    events: dict[str, Any] | None = None       # theme, trigger hits, per-name legs (events.json)

    @property
    def used_macro(self) -> bool:
        return bool(self.macro and self.macro.used_macro)

    @property
    def used_arag(self) -> bool:
        return bool(self.arag and self.arag.used_arag)

    @property
    def used_news(self) -> bool:
        return bool(self.news and self.news.applied)

    @property
    def used_crowding(self) -> bool:
        return bool(self.crowding and self.crowding.applied)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario, "severity": self.severity,
            "severity_scale": self.severity_scale,
            "driver_sources": self.driver_sources, "overlay_sources": self.overlay_sources,
            "used_macro": self.used_macro, "used_arag": self.used_arag, "used_news": self.used_news,
            "used_crowding": self.used_crowding,
            "macro": self.macro.to_dict() if self.macro else None,
            "arag": self.arag.to_dict() if self.arag else None,
            "news": self.news.to_dict() if self.news else None,
            "crowding": self.crowding.to_dict() if self.crowding else None,
            "events": self.events,
        }


class DualRagEnrichmentProvider:
    def __init__(self, *, macro_provider: MacroScenarioProvider | None = None,
                 arag_provider: AragCompanyProvider | None = None,
                 news_resolution: NewsResolution | None = None,
                 crowding_resolution: CrowdingResolution | None = None,
                 project_root: Path | None = None,
                 scenario_config: dict[str, Any] | None = None,
                 events: ScenarioEvents | None = None):
        self.macro = macro_provider
        self.arag = arag_provider
        # News + crowding are resolved once per run (PIT to the run's as_of) and shared across
        # scenarios/tiers; they scale the ARag idiosyncratic legs (mode-(ii), composed). None -> no-op.
        self.news = news_resolution
        self.crowding = crowding_resolution
        # ``events``: theme-relevant news/crowding and trigger hits (events.json). None -> the
        # theme-agnostic legs-only behaviour of composition rule dualrag-v2.
        self.events = events
        # ``scenario_config``: the catalog's view with replays resolved (``ScenarioCatalog.scenario_config``).
        self.static = scenario_config if scenario_config is not None else load_scenario_config(project_root)
        self._macro_cache: dict[str, DriverResolution] = {}
        self._arag_cache: dict[str, AragResolution] = {}

    @property
    def macro_resolutions(self) -> dict[str, DriverResolution]:
        """The MacroRAG resolution of every scenario resolved so far (a copy)."""
        return dict(self._macro_cache)

    @property
    def arag_resolutions(self) -> dict[str, AragResolution]:
        """The XbrlRag resolution of every scenario resolved without a stop so far (a copy)."""
        return dict(self._arag_cache)

    def requires_macro(self, name: str) -> bool:
        """Whether ``arag.json`` lists "macro" in this scenario's ``required_services``."""
        return self._requires_macro(name)

    def _idio_parts(self, factor: str, name: str | None = None) -> dict[str, tuple[float, dict[str, Any]]]:
        """News and crowding multipliers (mode-(ii)) for an ``eq:<TICKER>`` company leg.

        With ``events`` configured and a scenario ``name``, both are theme-relevant: news counts only
        the scenario's 8-K items (and tone where the theme uses it), crowding applies only to a
        crowding theme and a reliable name.

        Returns ``{"news": (mult, provenance), "crowding": (mult, provenance)}`` holding only the
        parts that move the leg; empty when neither applies (leg unchanged)."""
        if not fk.is_equity(factor):
            return {}
        ticker = fk.equity_name(factor)
        parts: dict[str, tuple[float, dict[str, Any]]] = {}
        if self.events is not None and name is not None:
            tk = ticker.upper()
            nleg = self.events.news_legs(name).get(tk)
            if nleg is not None and nleg.multiplier != 1.0:
                parts["news"] = (nleg.multiplier, nleg.to_dict())
            cmult = self.events.crowding_mult(name)
            if tk in cmult and self.crowding is not None:
                tleg = self.crowding.legs[tk]
                parts["crowding"] = (cmult[tk], dict(tleg.to_dict(), reliable=True))
            return parts
        if self.news is not None and self.news.applied:
            leg = self.news.leg_for(ticker)
            if leg is not None and leg.multiplier != 1.0:
                parts["news"] = (leg.multiplier, leg.to_dict())
        if self.crowding is not None and self.crowding.applied:
            cleg = self.crowding.leg_for(ticker)
            if cleg is not None and cleg.multiplier != 1.0:
                parts["crowding"] = (cleg.multiplier, cleg.to_dict())
        return parts

    def _macro_resolution(self, name: str) -> DriverResolution | None:
        if self.macro is None:
            return None
        if name not in self._macro_cache:
            self._macro_cache[name] = self.macro.resolve(name)
        return self._macro_cache[name]

    def _requires_macro(self, name: str) -> bool:
        """Whether ``arag.json`` lists "macro" in this scenario's ``required_services``."""
        scen_cfg: dict[str, Any] = getattr(self.arag, "scen_cfg", None) or {}
        required = (scen_cfg.get(name) or {}).get("required_services", [])
        return "macro" in {str(s).lower() for s in required}

    def _check_required_macro(self, name: str, macro_res: DriverResolution | None) -> None:
        """Stop the scenario (fail-loud, decision 2) if it requires macro and none was applied.

        Raises:
            ScenarioGenerationError: with the macro provider's fallback reason
                (macro_synthesis_failed | macro_unavailable | macro_gate_failed |
                macro_not_configured) and its detail, e.g. the gate findings.
        """
        if not self._requires_macro(name):
            return
        if macro_res is None:
            raise ScenarioGenerationError(name, FALLBACK_NOT_CONFIGURED, "no MacroRAG provider wired")
        if not macro_res.used_macro:
            raise ScenarioGenerationError(name, macro_res.fallback_reason or FALLBACK_UNAVAILABLE,
                                          macro_res.fallback_detail)

    def _arag_resolution(self, name: str) -> AragResolution | None:
        if self.arag is None:
            return None
        if name not in self._arag_cache:
            self._arag_cache[name] = self.arag.resolve(name)   # may raise ScenarioGenerationError
        return self._arag_cache[name]

    def layer_inputs(self, name: str) -> dict[str, Any]:
        """Resolve every service input of one scenario once (cached), for ``compose_definition``.

        Returns the keyword inputs of ``ablation.compose_definition`` other than ``layers``,
        ``tier_scale`` and ``state_mult``, plus the resolutions (``macro_res``, ``arag_res``).

        Raises:
            ScenarioGenerationError: A required MacroRAG is missing or failed, or ARag failed
                (fail-loud, as in ``definition_for``).
        """
        static_spec = self.static["scenarios"][name]
        macro_res = self._macro_resolution(name)
        self._check_required_macro(name, macro_res)
        arag_res = self._arag_resolution(name)
        calibrated = dict(arag_res.company_drivers) if arag_res is not None else {}
        # The seed is the leg before XBRL fact calibration; a source without one was not calibrated.
        seeds = {f: float((arag_res.sources.get(f) or {}).get("seed_move", move))
                 for f, move in calibrated.items()} if arag_res is not None else {}
        news_mult: dict[str, float] = {}
        crowding_mult: dict[str, float] = {}
        for factor in calibrated:
            parts = self._idio_parts(factor, name)
            if "news" in parts:
                news_mult[factor] = parts["news"][0]
            if "crowding" in parts:
                crowding_mult[factor] = parts["crowding"][0]
        out: dict[str, Any] = {
            "authored_drivers": {k: to_canonical(k, v) for k, v in static_spec.get("drivers", {}).items()},
            "authored_overlays": {k: to_canonical(k, v) for k, v in static_spec.get("overlays", {}).items()},
            "macro_drivers": dict(macro_res.drivers) if macro_res is not None else None,
            "seed_legs": seeds, "calibrated_legs": calibrated,
            "news_mult": news_mult, "crowding_mult": crowding_mult,
            "macro_res": macro_res, "arag_res": arag_res,
        }
        if self.events is not None:          # consumed by anchoring.leg_transform, never by compose
            out["equity_events"] = self._equity_events(name, static_spec)
        return out

    def _equity_events(self, name: str, static_spec: dict[str, Any]) -> dict[str, Any] | None:
        """Inputs for scaling the non-leg equity drivers (``anchoring.leg_transform``); None without events."""
        if self.events is None:
            return None
        sev = static_spec.get("severity") or {}
        return {"news": self.events.news_mult(name), "crowding": self.events.crowding_mult(name),
                "theme": self.events.theme(name), "excluded": self.events.config.excluded,
                "lead": sev.get("lead")}

    def definition_for(self, name: str, *, severity: str = "adverse", severity_scale: float = 1.0,
                       layers: frozenset[str] | None = None, state_mult: float = 1.0,
                       leg_transform: Callable[[dict[str, Any], frozenset[str]], dict[str, Any]] | None = None,
                       ) -> tuple[ScenarioDefinition, DualResolution]:
        """Compose one scenario tier from the resolved services (``ablation.compose_definition``).

        Args:
            name: Scenario id.
            severity: Tier name.
            severity_scale: Tier scale; production passes tier x state here with ``state_mult=1``.
            layers: Enabled layer ids; None = every layer (production).
            state_mult: GPR x throughput multiplier, applied only when ``"state"`` is enabled.
            leg_transform: Optional hook on the resolved inputs before composition (severity
                anchoring: company-specific seed and the stacked-multiplier cap), given the layers.

        Raises:
            ScenarioGenerationError: Fail-loud service failure, or no drivers after the merge.
        """
        static_spec = self.static["scenarios"][name]
        enabled = frozenset(LAYERS) if layers is None else layers
        inputs = self.layer_inputs(name)
        macro_res: DriverResolution | None = inputs.pop("macro_res")
        arag_res: AragResolution | None = inputs.pop("arag_res")
        if leg_transform is not None:
            inputs = leg_transform(inputs, enabled)
        inputs.pop("equity_events", None)
        try:
            drivers, overlays, scale = compose_definition(
                layers=enabled, tier_scale=severity_scale, state_mult=state_mult, **inputs)
        except ValueError as exc:
            if "no drivers" not in str(exc):
                raise
            raise ScenarioGenerationError(
                name, "no drivers after dual-RAG merge",
                "scenario has no authored/macro/ARag drivers to propagate "
                "(check scenarios.json, MacroRAG synthesis and arag.json)") from exc
        severity_scale = scale

        # --- provenance: which service each applied leg came from ------------------------- #
        if "macrorag" in enabled and macro_res is not None:
            driver_sources: dict[str, dict[str, Any]] = dict(macro_res.sources)  # static | macrorag
        else:
            driver_sources = {k: {"origin": "authored"} for k in inputs["authored_drivers"]}
        overlay_sources: dict[str, dict[str, Any]] = {k: {"origin": "authored"}
                                                      for k in inputs["authored_overlays"]}
        if arag_res is not None and inputs["seed_legs"] and "xbrlrag_narrative" in enabled:
            overlay_mode = not set(drivers) & set(inputs["seed_legs"])
            for factor in inputs["seed_legs"]:
                driver_sources.pop(factor, None)
                src = dict(arag_res.sources[factor])
                src["mode"] = "overlay" if overlay_mode else "driver"
                src["applied_move"] = overlays[factor] if overlay_mode else drivers[factor]
                calib = {k: v[1] for k, v in self._idio_parts(factor, name).items() if k in enabled}
                if calib:
                    src["calibration"] = calib
                (overlay_sources if overlay_mode else driver_sources)[factor] = src

        defn = ScenarioDefinition(
            name=name, drivers=drivers,
            method=str(static_spec.get("method", METHOD_CONDITIONAL)),
            severity=severity, severity_scale=severity_scale, overlays=overlays,
            narrative=str(static_spec.get("narrative", "")),
            trigger=str(static_spec.get("trigger", "")),
            horizon_days=int(static_spec.get("horizon_days", 10)),
            version=compute_scenario_version(dict(static_spec)),
        )
        dual = DualResolution(
            scenario=name, severity=severity, severity_scale=severity_scale,
            driver_sources=driver_sources, overlay_sources=overlay_sources,
            macro=macro_res, arag=arag_res, news=self.news, crowding=self.crowding,
            events=self.events.provenance(name) if self.events is not None else None,
        )
        if layers is None and (dual.used_macro or dual.used_arag):   # production call only, not per subset
            logger.info("  %-22s [%-7s] dual-RAG: macro=%s arag=%s news=%s crowding=%s (%d company leg(s))",
                        name, severity, dual.used_macro, dual.used_arag, dual.used_news,
                        dual.used_crowding, len(arag_res.company_drivers) if arag_res else 0)
        return defn, dual
