"""MacroScenarioProvider: resolve a scenario's drivers from MacroRAG (S4).

Implements the design's MacroRAG handoff (§9, catalog §9B): MacroRAG sets the macro-leg
**drivers** (direction/size + citations); the conditional-covariance engine (S1) then
propagates them and the existing engine prices them. The expert/house equity drivers and
overlays from the authored `scenarios.json` are kept and merged with the macro legs.

Robust to MacroRAG being absent: if no client is wired, the synthesis call errors, the
shock vector is unavailable, or the freshness/citation GATE fails, the provider falls back
to the authored static drivers — the stress run always produces a result, and the
resolution records exactly which legs were macro-sourced vs static, and why it fell back
(``fallback_reason``). Plain ``--macro`` mode never raises; the dual-RAG provider turns a
fallback into a per-scenario stop when the scenario requires macro (``arag.json``).
Fields MacroRAG flagged with a hard validation code are dropped before mapping. The request
carries the scenario's ``horizon_days``, ``expect_fields`` and ``directions`` so MacroRAG can
check evidence horizons and signs. When MacroRAG supplies part of the UST curve, the authored
IR tenors it did not supply are superseded (propagated by the engine), keeping the curve whole.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.common.config.models import ConfigError
from src.common.obs import metrics as obs_metrics

from .. import factor_keys as fk
from ..config import load_scenario_config
from ..scenario_gen import METHOD_CONDITIONAL, ScenarioDefinition, to_canonical
from ..versioning import compute_scenario_version
from .client import MacroRAGClient
from .gate import MacroGate, MacroGateResult
from .mapping import DEFAULT_MOVE_BASE, to_canonical_drivers, unmapped_fields
from .models import MacroFilter, MacroSynthesis, point_in_time_filter

logger = logging.getLogger("scenariogen.macro")

# A macro-attempted scenario that ends up on static drivers is a degraded run, not a silent
# one: surface why (synthesis call failed / shock vector unavailable / freshness-citation GATE).
SCEN_MACRO_FALLBACK = obs_metrics.counter(
    "scenariogen", "macro_fallback_total", "Scenarios that fell back to static drivers", ("reason",))

FALLBACK_NOT_CONFIGURED = "macro_not_configured"     # no client, or no macro.json entry
FALLBACK_SYNTHESIS_FAILED = "macro_synthesis_failed"  # the call raised (HTTP, timeout, parse)
FALLBACK_UNAVAILABLE = "macro_unavailable"            # refused, or no usable shock-vector field
FALLBACK_GATE_FAILED = "macro_gate_failed"            # freshness/citation GATE findings
FALLBACK_FILTER_INVALID = "macro_filter_invalid"      # the scenario's filter can't be pinned to as_of

_DIRECTIONS = ("up", "down")   # MacroRAG synthesis.directions values (its `positive_means` sign)


@dataclass
class DriverResolution:
    """The merged driver set for one scenario, with per-leg provenance."""

    scenario: str
    drivers: dict[str, float]                          # canonical key -> move (final)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    used_macro: bool = False
    gate: MacroGateResult | None = None
    macro_summary: str = ""
    world_view_id: str = ""                            # MacroRAG snapshot id (provenance pin)
    # Why the macro legs were not applied ("" when used_macro): one of the FALLBACK_* reasons,
    # which are also the dual-RAG ScenarioGenerationError reasons when macro is required.
    fallback_reason: str = ""
    fallback_detail: str = ""
    dropped_fields: dict[str, list[str]] = field(default_factory=dict)   # hard-invalid field -> codes
    # Authored ir:* drivers removed because MacroRAG supplied part of the curve (left to the
    # engine to propagate from the MacroRAG tenors); sorted, [] when nothing was removed.
    superseded_static: list[str] = field(default_factory=list)
    # Usable response fields with no canonical factor (dropped by the mapping); sorted, [] when
    # every field mapped or no response was received.
    unmapped_fields: list[str] = field(default_factory=list)
    # The release-date cap sent with the request (macro-as-of-filter): the run's as_of or an
    # earlier configured date_to; "" when no request was made.
    date_to: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario, "drivers": dict(self.drivers),
            "sources": self.sources, "used_macro": self.used_macro,
            "gate": self.gate.to_dict() if self.gate else None,
            "macro_summary": self.macro_summary,
            "world_view_id": self.world_view_id,
            "fallback_reason": self.fallback_reason,
            "dropped_fields": {k: list(v) for k, v in self.dropped_fields.items()},
            "superseded_static": list(self.superseded_static),
            "unmapped_fields": list(self.unmapped_fields),
            "date_to": self.date_to,
        }


def _validate_directions(scenarios: dict[str, Any]) -> None:
    """Check every scenario's ``directions`` against ``"up"``/``"down"`` and its ``expect_fields``.

    Raises:
        ConfigError: a direction value other than "up"/"down", or a key not in expect_fields.
    """
    for name, spec in scenarios.items():
        directions = spec.get("directions") or {}
        expected = set(spec.get("expect_fields") or [])
        for fld, way in directions.items():
            if way not in _DIRECTIONS:
                raise ConfigError(f"macro.json scenarios.{name}.directions.{fld}: {way!r} is not "
                                  f"'up' or 'down'")
            if fld not in expected:
                raise ConfigError(f"macro.json scenarios.{name}.directions.{fld}: not in expect_fields")


def _supersede_unsupplied_curve(res: DriverResolution, macro_factors: set[str]) -> None:
    """Keep the UST curve coherent once MacroRAG has supplied part of it.

    If any applied MacroRAG leg is an IR curve node, the authored ``ir:*`` drivers MacroRAG did not
    supply are removed, so the conditional-covariance engine propagates them from the MacroRAG
    tenors instead of splicing authored and MacroRAG tenors into one curve.
    """
    if not any(fk.is_ir(f) for f in macro_factors):
        return
    res.superseded_static = sorted(f for f in res.drivers if fk.is_ir(f) and f not in macro_factors)
    for f in res.superseded_static:
        del res.drivers[f]
        res.sources.pop(f, None)


class MacroScenarioProvider:
    def __init__(self, config: dict[str, Any], *, as_of: date,
                 client: MacroRAGClient | None = None, project_root: Path | None = None,
                 scenario_config: dict[str, Any] | None = None, no_cache: bool = False):
        self.cfg = config
        self.no_cache = no_cache                        # bypass MacroRAG's answer caches (stability runs)
        self.client = client
        self.scen_cfg: dict[str, Any] = config.get("scenarios", {})
        _validate_directions(self.scen_cfg)
        self.move_base = float(config.get("units", {}).get("move_base", DEFAULT_MOVE_BASE))
        self.gate = MacroGate(as_of, **config.get("gate", {}))
        self.as_of = as_of                              # retrieval is pinned to it (macro-as-of-filter)
        # ``scenario_config``: the catalog's view with replays resolved (``ScenarioCatalog.scenario_config``).
        self.static = scenario_config if scenario_config is not None else load_scenario_config(project_root)

    def resolve(self, name: str) -> DriverResolution:
        static_spec = self.static["scenarios"][name]
        drivers = {k: to_canonical(k, v) for k, v in static_spec.get("drivers", {}).items()}
        sources: dict[str, dict[str, Any]] = {k: {"origin": "static"} for k in drivers}
        res = DriverResolution(scenario=name, drivers=drivers, sources=sources)

        spec = self.scen_cfg.get(name)
        if self.client is None or not spec:
            res.fallback_reason = FALLBACK_NOT_CONFIGURED
            return res                                  # no macro wiring -> static only

        try:
            filters = point_in_time_filter(MacroFilter.from_config(spec.get("filters", {})), self.as_of)
        except ValueError as exc:
            SCEN_MACRO_FALLBACK.labels(reason="filter_invalid").inc()
            res.fallback_reason, res.fallback_detail = FALLBACK_FILTER_INVALID, str(exc)
            return res                                  # a misconfigured window: never silently unpinned
        res.date_to = filters.date_to or ""
        synth, error = self._synthesize(name, spec, int(static_spec.get("horizon_days", 10)), filters)
        if synth is None:
            res.fallback_reason, res.fallback_detail = FALLBACK_SYNTHESIS_FAILED, error
            return res                                  # synthesis call failed (counted below)
        res.dropped_fields = dict(synth.dropped)
        if synth.dropped:
            logger.warning("  macro fields dropped for %s (hard validation codes): %s", name,
                           ", ".join(f"{k}={'/'.join(v)}" for k, v in synth.dropped.items()))
        res.unmapped_fields = unmapped_fields(synth.fields)
        if res.unmapped_fields:
            logger.info("  macro fields with no canonical factor for %s: %s", name,
                        ", ".join(res.unmapped_fields))
        # A refusal, an empty shock vector, or only hard-invalid / unmapped fields leaves nothing
        # to apply: all are "unavailable".
        macro_drivers = to_canonical_drivers(synth.fields, self.move_base) if synth.available else []
        if not macro_drivers:
            SCEN_MACRO_FALLBACK.labels(reason="unavailable").inc()
            res.fallback_reason = FALLBACK_UNAVAILABLE
            res.fallback_detail = "refused" if synth.refused else "no usable shock-vector field"
            return res                                  # no shock vector -> fallback

        res.gate = self.gate.check(macro_drivers, synth.chunks)
        res.macro_summary = synth.summary
        res.world_view_id = synth.world_view_id
        if not res.gate.passed:
            SCEN_MACRO_FALLBACK.labels(reason="gate_failed").inc()
            logger.warning("  macro GATE failed for %s -> static fallback (%d finding(s))",
                           name, len(res.gate.findings))
            res.fallback_reason = FALLBACK_GATE_FAILED
            res.fallback_detail = "; ".join(res.gate.findings)
            return res

        for d in macro_drivers:                          # macro legs override/add to static
            res.drivers[d.factor] = d.move
            res.sources[d.factor] = d.as_source()
        _supersede_unsupplied_curve(res, {d.factor for d in macro_drivers})
        if res.superseded_static:
            logger.info("  macro curve for %s: authored %s superseded (propagated from MacroRAG tenors)",
                        name, ", ".join(res.superseded_static))
        res.used_macro = True
        return res

    def definition_for(self, name: str, *, severity: str = "adverse",
                       severity_scale: float = 1.0) -> tuple[ScenarioDefinition, DriverResolution]:
        """Build a generator-ready `ScenarioDefinition` from the resolved drivers."""
        res = self.resolve(name)
        static_spec = self.static["scenarios"][name]
        overlays = {k: to_canonical(k, v) for k, v in static_spec.get("overlays", {}).items()}
        defn = ScenarioDefinition(
            name=name, drivers=dict(res.drivers),
            method=str(static_spec.get("method", METHOD_CONDITIONAL)),
            severity=severity, severity_scale=severity_scale, overlays=overlays,
            narrative=str(static_spec.get("narrative", "")),
            trigger=str(static_spec.get("trigger", "")),
            horizon_days=int(static_spec.get("horizon_days", 10)),
            version=compute_scenario_version(dict(static_spec)),
        )
        return defn, res

    def _synthesize(self, name: str, spec: dict[str, Any], horizon_days: int,
                    filters: MacroFilter) -> tuple[MacroSynthesis | None, str]:
        """Call MacroRAG synthesis with the point-in-time ``filters``; (synthesis, "") or (None, error)."""
        question = spec.get("synthesis_question", "")
        if self.client is None:
            return None, "no MacroRAG client"
        try:
            synth = self.client.synthesize(question, filters, horizon_days=horizon_days,
                                           fields=list(spec.get("expect_fields") or []),
                                           directions=dict(spec.get("directions") or {}),
                                           no_cache=self.no_cache)
            return synth, ""
        except Exception as exc:  # network/HTTP/parse failure must not break the run
            SCEN_MACRO_FALLBACK.labels(reason="synthesis_failed").inc()
            logger.warning("  macro synthesis call failed for %s: %s -> static fallback",
                           name, exc)
            return None, f"{type(exc).__name__}: {exc}"
