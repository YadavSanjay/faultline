"""AragCompanyProvider: resolve a scenario's company legs from ARag (dual-RAG P1).

ARag supplies the **company** legs of a scenario: for each configured company it retrieves the
disclosed Risk Factors (evidence + citations), seeds the leg from the authored baseline (or a
config ``seed_move``), and — where a ``calibration`` is configured — deterministically scales
that seed by a cited ARag fact (``calibrate.resolve_multiplier``). Every leg records its ARag
provenance so the scenario summary can state exactly what ARag contributed.

Fail-loud (decision 2), per scenario, via ``ScenarioGenerationError`` (the runner catches it,
logs/documents, and skips just that scenario):
  * a required company has no seed magnitude (config error);
  * an ARag ``narrative_search`` call errors or exceeds the 10-min ceiling;
  * the ARag evidence GATE fails while ARag is a *required* service for the scenario.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.common.obs import metrics as obs_metrics

from ..config import load_scenario_config
from ..errors import ScenarioGenerationError
from ..scenario_gen import to_canonical
from .calibrate import CompanyCalibration, resolve_multiplier
from .client import AragClient, AragTransportError
from .gate import AragGate, AragGateResult
from .mapping import AragCompanyRisk, company_risk_from_result, eq_key

logger = logging.getLogger("scenariogen.arag")

SCEN_ARAG_STOP = obs_metrics.counter(
    "scenariogen", "arag_stop_total", "Scenarios stopped by the ARag path", ("reason",))


@dataclass
class AragResolution:
    """The company-leg set ARag resolved for one scenario, with per-leg provenance."""

    scenario: str
    company_drivers: dict[str, float] = field(default_factory=dict)   # eq:<TKR> -> scaled move
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    gate: AragGateResult | None = None
    used_arag: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario, "company_drivers": dict(self.company_drivers),
            "sources": self.sources, "used_arag": self.used_arag,
            "gate": self.gate.to_dict() if self.gate else None,
        }


class AragCompanyProvider:
    def __init__(self, config: dict[str, Any], *, as_of: date,
                 client: AragClient, project_root: Path | None = None,
                 scenario_config: dict[str, Any] | None = None):
        self.cfg = config
        self.client = client
        self.scen_cfg: dict[str, Any] = config.get("scenarios", {})
        self.defaults: dict[str, Any] = config.get("defaults", {})
        self.gate = AragGate(as_of, **config.get("gate", {}))
        # ``scenario_config``: the catalog's view with replays resolved (``ScenarioCatalog.scenario_config``).
        self.static = scenario_config if scenario_config is not None else load_scenario_config(project_root)

    def resolve(self, name: str) -> AragResolution:
        scen = self.scen_cfg.get(name, {})
        companies: list[dict[str, Any]] = list(scen.get("companies", []))
        required = "arag" in {s.lower() for s in scen.get("required_services", [])}
        res = AragResolution(scenario=name)
        if not companies:
            return res                                  # macro-only scenario -> no company legs

        legs: list[AragCompanyRisk] = []
        for c in companies:
            leg = self._resolve_company(name, c)
            legs.append(leg)
            res.company_drivers[leg.factor] = leg.move
            src = leg.as_source()
            src["seed_move"] = self._last_seed
            if self._last_calibration is not None:
                src["calibration"] = self._last_calibration
            res.sources[leg.factor] = src

        res.gate = self.gate.check(legs)
        res.used_arag = bool(res.company_drivers)
        if not res.gate.passed:
            if required:
                SCEN_ARAG_STOP.labels(reason="gate_failed").inc()
                raise ScenarioGenerationError(
                    name, "ARag evidence GATE failed",
                    f"{len(res.gate.findings)} finding(s): {'; '.join(res.gate.findings)}")
            logger.warning("  %-22s ARag GATE flagged (not required) (%d finding(s))",
                           name, len(res.gate.findings))
        return res

    # -- internals ----------------------------------------------------------- #
    def _resolve_company(self, name: str, c: dict[str, Any]) -> AragCompanyRisk:
        ticker = str(c["ticker"]).upper()
        seed = self._seed_move(name, ticker, c)
        self._last_seed = seed
        self._last_calibration = None

        narrative = self._search(name, ticker, c)
        move = seed
        if c.get("calibration"):
            cal = CompanyCalibration.from_config(dict(c["calibration"]))
            cal_res = resolve_multiplier(self.client, cal, ticker=ticker)
            move = seed * cal_res.multiplier
            self._last_calibration = cal_res.to_dict()
            logger.info("  %-22s eq:%s seed %.3f x%.3f -> %.3f (%s)", name, ticker, seed,
                        cal_res.multiplier, move, cal_res.note)

        return company_risk_from_result(
            narrative, move=move, risk_factor=str(c.get("risk_factor", "")),
            rationale=str(c.get("rationale", "")),
            top_n_chunks=int(self.defaults.get("top_n_chunks", 3)))

    def _seed_move(self, name: str, ticker: str, c: dict[str, Any]) -> float:
        """Seed magnitude: config ``seed_move`` (decimal return), else the authored baseline."""
        if "seed_move" in c and c["seed_move"] is not None:
            return float(c["seed_move"])
        drivers = self.static.get("scenarios", {}).get(name, {}).get("drivers", {})
        key = eq_key(ticker)
        if key in drivers:
            return float(to_canonical(key, drivers[key]))
        SCEN_ARAG_STOP.labels(reason="no_seed").inc()
        raise ScenarioGenerationError(
            name, f"company {ticker} has no seed magnitude",
            f"add companies[].seed_move for {ticker} in arag.json or an eq:{ticker} driver "
            "in scenarios.json")

    def _search(self, name: str, ticker: str, c: dict[str, Any]) -> Any:
        query = str(c.get("narrative_query", ""))
        section = c.get("section_hint", self.defaults.get("section_hint", "Risk Factors"))
        fiscal_years = c.get("fiscal_years")
        top_k = int(self.defaults.get("top_k", 8))
        try:
            return self.client.narrative_search(
                ticker, query, section_hint=section,
                fiscal_years=list(fiscal_years) if fiscal_years else None, top_k=top_k)
        except AragTransportError as exc:  # timeout past the 10-min ceiling / HTTP / breaker
            SCEN_ARAG_STOP.labels(reason="narrative_error").inc()
            raise ScenarioGenerationError(
                name, f"ARag narrative_search failed for {ticker}", str(exc)) from exc

    _last_seed: float = 0.0
    _last_calibration: dict[str, Any] | None = None
