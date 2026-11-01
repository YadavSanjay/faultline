"""Named-scenario catalog with severity tiers (S2).

Loads the authored scenario definitions + the covariance window policy from
``configs/scenariogen/`` and the estimated Σ from `FactorHistory`, and materializes any
named scenario at any severity tier (mild / adverse / severe) into a complete, correlated
`MaterializedScenario`. The tier is a multiplier on the narrative drivers (§4E / §6); the
conditional-covariance propagation then scales coherently with it.

A scenario with a ``replay`` block (``start``, ``end``, ``idiosyncratic``) is a historical replay: its
drivers are the episode's observed moves of the Σ factors (``replay.py``) and its horizon the market
factor's trading days over the window, resolved here when the catalog loads. The resolved specs are what
the providers, the store and the version hash see, so a replay's version changes if the data under its
window is revised. The ``sensitivities`` block of ``scenarios.json`` (the on-demand sensitivity library)
resolves like a scenario but is not in ``names()``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.config.models import ConfigError
from src.pricer.risk.factor_history import FactorHistory

from .config import load_covariance_config, load_scenario_config
from .covariance import FactorCovariance, covariance_from_history, crisis_covariance
from .errors import ScenarioGenerationError
from .factor_vector import MaterializedScenario
from .implied_corr import resolve_regime
from .replay import episode_moves, replay_drivers, trading_days
from .scenario_gen import METHOD_CONDITIONAL, ScenarioDefinition, ScenarioGenerator

# The tier whose Σ is the crisis Σ (design §3.3: "the severe tier uses crisis Σ").
CRISIS_TIER = "severe"
# Covariance sources of the severe tier (issuer-scenario-support): a severity block's ``severe_sigma``.
CRISIS_SOURCE = "crisis"
PRODUCTION_SOURCE = "production"
CRISIS_UNAVAILABLE = "crisis_sigma_unavailable"

# The market factor whose trading days set a replay's horizon and whose history must cover the window.
REPLAY_MARKET_FACTOR = "eq:XLK"
REPLAY_UNCOVERED = "replay_window_uncovered"
# Lookback that keeps every aligned day (the crisis windows sit years back).
_ALL_DAYS = 1_000_000


def resolve_replay(name: str, spec: Mapping[str, Any], series: Mapping[str, pd.Series],
                   factors: Sequence[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Turns a replay spec into an ordinary spec whose drivers are the episode's observed moves.

    Args:
        name: Scenario id (for the error).
        spec: The ``scenarios.json`` entry with a ``replay`` block.
        series: Factor key -> daily moves (``FactorHistory.factor_series(include_style=True)``).
        factors: The Σ factors; only their moves become drivers.

    Returns:
        ``(resolved_spec, replay_info)``: the spec with canonical ``{"move": x}`` drivers, the market
        factor's trading days as ``horizon_days`` and the conditional-covariance method (overlays as
        authored), and the provenance block (window, horizon, observed factor count, excluded
        idiosyncratic moves with their reasons, factors whose history starts inside the window).

    Raises:
        ScenarioGenerationError: The on-disk market history doesn't cover the window.
    """
    rp = spec["replay"]
    start, end = date.fromisoformat(str(rp["start"])), date.fromisoformat(str(rp["end"]))
    idio = {str(k): str(v) for k, v in (rp.get("idiosyncratic") or {}).items()}
    market = series.get(REPLAY_MARKET_FACTOR)
    if market is None or market.dropna().empty:
        raise ScenarioGenerationError(name, REPLAY_UNCOVERED, f"no {REPLAY_MARKET_FACTOR} history on disk")
    dates = pd.to_datetime(pd.Index(market.dropna().index).astype(str))
    first, last = dates.min().date(), dates.max().date()
    if first > start or last < end:
        raise ScenarioGenerationError(
            name, REPLAY_UNCOVERED,
            f"window {start}..{end} outside the {REPLAY_MARKET_FACTOR} history {first}..{last}")
    moves = episode_moves(series, start, end, factors=list(factors))
    drivers, excluded = replay_drivers(moves, idio)
    horizon = trading_days(market, start, end)
    # A factor whose history starts inside the window replays only the part it covers; it is kept (its
    # observed move beats a calm-regime Σ fill) and listed with its first date.
    partial: dict[str, str] = {}
    for f in drivers:
        first_f = pd.to_datetime(pd.Index(series[f].dropna().index).astype(str)).min().date()
        if first_f > start:
            partial[f] = first_f.isoformat()
    resolved = dict(spec)
    resolved["drivers"] = {k: {"move": v} for k, v in sorted(drivers.items())}
    resolved["horizon_days"] = horizon
    resolved["method"] = METHOD_CONDITIONAL
    info = {
        "start": start.isoformat(), "end": end.isoformat(), "horizon_days": horizon,
        "n_observed": len(drivers),
        "excluded": {k: {"move": v, "reason": idio[k]} for k, v in sorted(excluded.items())},
        "partial": dict(sorted(partial.items())),
        # Applied as one instantaneous shock to today's book: no expiries, carry or rebalancing.
        "instantaneous": True,
    }
    return resolved, info


def severe_covariance_source(spec: Mapping[str, Any]) -> str:
    """Returns the source of the covariance a scenario's severe tier materializes with.

    Args:
        spec: A ``scenarios.json`` scenario entry.

    Returns:
        ``spec["severity"]["severe_sigma"]`` when the spec has a ``severity`` mapping holding that key,
        else ``"crisis"``. The value is ``"crisis"`` or ``"production"``.

    Raises:
        ConfigError: ``severe_sigma`` is present but is not ``"crisis"`` or ``"production"``.
    """
    sev = spec.get("severity")
    if not isinstance(sev, Mapping) or "severe_sigma" not in sev:
        return CRISIS_SOURCE
    value = sev["severe_sigma"]
    if not isinstance(value, str) or value not in (CRISIS_SOURCE, PRODUCTION_SOURCE):
        raise ConfigError(f"severity.severe_sigma must be 'crisis' or 'production', got {value!r}")
    return value


@dataclass
class ScenarioCatalog:
    """The standing set of named scenarios, resolvable at any severity tier."""

    covariance: FactorCovariance
    specs: dict[str, dict[str, Any]]          # scenario_id -> config spec (replays resolved)
    severity_tiers: dict[str, float]          # tier label -> driver multiplier
    sensitivities: dict[str, dict[str, Any]] = field(default_factory=dict)    # on-demand library
    replays: dict[str, dict[str, Any]] = field(default_factory=dict)          # id -> replay provenance
    errors: dict[str, ScenarioGenerationError] = field(default_factory=dict)  # unresolvable replays
    crisis: FactorCovariance | None = None                                    # severe-tier Σ
    crisis_coverage: dict[str, int] = field(default_factory=dict)             # window -> aligned days
    crisis_error: str | None = None                                           # why it can't be built

    @classmethod
    def from_config(cls, history: FactorHistory, *, lookback: int | None = None,
                    project_root: Path | None = None) -> ScenarioCatalog:
        cov_cfg = load_covariance_config(project_root)
        scen_cfg = load_scenario_config(project_root)
        # F7: resolve the market-implied Σ regime, which sets the shrinkage from the current
        # implied-correlation percentile. Disabled/absent -> the fixed config shrinkage (byte-
        # identical to pre-F7), with the regime state still stamped for provenance.
        fixed_shrinkage = float(cov_cfg.get("shrinkage", 0.0))
        # A point-in-time history (the event replay's PitFactorHistory) carries a cutoff: read the
        # regime as of that day so the replay's Σ doesn't see a later COR3M level. Other runs: latest.
        cutoff = getattr(history, "cutoff", None)
        as_of = pd.Timestamp(cutoff).date() if cutoff is not None else None
        regime = resolve_regime(project_root, cov_cfg.get("implied_corr_regime"), fixed_shrinkage,
                                as_of=as_of)
        cov = covariance_from_history(
            history,
            lookback=int(lookback or cov_cfg.get("lookback_days", 250)),
            window=str(cov_cfg.get("window", "full")),
            ewma_lambda=float(cov_cfg.get("ewma_lambda", 0.94)),
            shrinkage=regime.shrinkage,
            crisis_windows=[tuple(w) for w in cov_cfg.get("crisis_windows", [])] or None,
            include_style=bool(cov_cfg.get("include_style", False)),
            regime=regime.as_dict() if regime.enabled else None,
        )
        # The severe tier propagates through crisis Σ (sample covariance over the configured crisis
        # windows of the full aligned history, lightly shrunk), so calm-regime shrinkage can't damp it.
        crisis: FactorCovariance | None = None
        coverage: dict[str, int] = {}
        crisis_error: str | None = None
        sev = cov_cfg.get("severe")
        if sev:
            panel = history.aligned_panel(_ALL_DAYS, include_style=bool(cov_cfg.get("include_style", False)))
            try:
                crisis, coverage = crisis_covariance(
                    panel, [(str(a), str(b)) for a, b in sev["windows"]],
                    shrinkage=float(sev.get("shrinkage", 0.0)), min_days=int(sev.get("min_days", 60)))
            except ValueError as exc:                # fail-loud per severe tier, not a calm-Σ fallback
                crisis_error = str(exc)
        specs = dict(scen_cfg.get("scenarios", {}))
        sens = dict(scen_cfg.get("sensitivities", {}))
        replays: dict[str, dict[str, Any]] = {}
        errors: dict[str, ScenarioGenerationError] = {}
        if any("replay" in s for s in (*specs.values(), *sens.values())):
            series = history.factor_series(include_style=True)
            for block in (specs, sens):
                for name, spec in block.items():
                    if "replay" not in spec:
                        continue
                    try:
                        block[name], replays[name] = resolve_replay(name, spec, series, cov.factors)
                    except ScenarioGenerationError as exc:      # fail-loud for this scenario only
                        errors[name] = exc
        return cls(
            covariance=cov,
            specs=specs,
            severity_tiers=dict(scen_cfg.get("severity_tiers", {"adverse": 1.0})),
            sensitivities=sens,
            replays=replays,
            errors=errors,
            crisis=crisis,
            crisis_coverage=coverage,
            crisis_error=crisis_error,
        )

    def names(self) -> list[str]:
        return list(self.specs)

    def sensitivity_names(self) -> list[str]:
        return list(self.sensitivities)

    def tiers(self) -> list[str]:
        return list(self.severity_tiers)

    def spec(self, name: str) -> dict[str, Any]:
        """The (replay-resolved) spec of a scenario or sensitivity.

        Raises:
            KeyError: Unknown name.
            ScenarioGenerationError: A replay whose window the on-disk history doesn't cover.
        """
        if name in self.errors:
            raise self.errors[name]
        if name in self.specs:
            return self.specs[name]
        if name in self.sensitivities:
            return self.sensitivities[name]
        raise KeyError(f"unknown scenario {name!r}; known: {self.names() + self.sensitivity_names()}")

    def covariance_for(self, tier: str, name: str = "*") -> FactorCovariance:
        """The Σ a tier materializes with: crisis Σ for the severe tier when configured, else production.

        A scenario whose severity block sets ``severe_sigma: "production"`` keeps the production Σ for its
        severe tier too (``severe_covariance_source``).

        Raises:
            ScenarioGenerationError: The severe tier's crisis Σ is configured but can't be built.
        """
        if tier != CRISIS_TIER or (self.crisis is None and self.crisis_error is None):
            return self.covariance
        spec = self.specs.get(name) or self.sensitivities.get(name)
        if spec is not None and severe_covariance_source(spec) == PRODUCTION_SOURCE:
            return self.covariance                      # an issuer event: production Σ (own history)
        if self.crisis is None:
            raise ScenarioGenerationError(name, CRISIS_UNAVAILABLE, str(self.crisis_error))
        return self.crisis

    def generator_for(self, tier: str, name: str = "*") -> ScenarioGenerator:
        return ScenarioGenerator(self.covariance_for(tier, name))

    def scenario_config(self) -> dict[str, Any]:
        """A ``scenarios.json``-shaped view with replays resolved, for the driver providers."""
        return {"severity_tiers": dict(self.severity_tiers),
                "scenarios": {**self.specs, **self.sensitivities}}

    def definition(self, name: str, severity: str, *,
                   severity_multiplier: float = 1.0) -> ScenarioDefinition:
        spec = self.spec(name)
        if severity not in self.severity_tiers:
            raise KeyError(f"unknown severity {severity!r}; known: {self.tiers()}")
        # ``severity_multiplier`` (default 1.0, a no-op) is the F8 geopolitical severity multiplier
        # a geopolitically-tagged scenario applies to its whole shock; it scales the tier magnitude.
        return ScenarioDefinition.from_config(
            name, spec,
            severity=severity, severity_scale=self.severity_tiers[severity] * severity_multiplier)

    def stamp(self, name: str, ms: MaterializedScenario) -> MaterializedScenario:
        """Adds the replay provenance block to a materialized replay (no-op otherwise)."""
        if name in self.replays:
            ms.provenance["replay"] = dict(self.replays[name])
        if ms.severity == CRISIS_TIER and self.crisis is not None:
            ms.provenance["crisis_coverage"] = dict(self.crisis_coverage)
        return ms

    def materialize(self, name: str, severity: str, *,
                    severity_multiplier: float = 1.0) -> MaterializedScenario:
        return self.stamp(name, self.generator_for(severity, name).materialize(
            self.definition(name, severity, severity_multiplier=severity_multiplier)))

    def materialize_all(self) -> list[MaterializedScenario]:
        """Every named scenario at every severity tier."""
        return [self.materialize(name, tier)
                for name in self.names() for tier in self.tiers()]
