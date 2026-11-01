"""Real pricing service for the LLM flow (P2).

Wraps the *existing* scenario machinery — the same objects `src/scenariogen/runner.py` builds —
so an LLM-calibrated `ScenarioDefinition` is materialized (covariance propagation) and fully
repriced (P&L + ES-under-shock capital) exactly like a static scenario. Heavy pricer imports
(QuantLib via `PricingEngine`) are deferred into the constructor, so importing this module is
cheap and the package `__init__` doesn't pull QuantLib; only the runner constructs it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .results import PricedScenario

if TYPE_CHECKING:
    from ..scenario_gen import ScenarioDefinition
    from ..stress import ScenarioStressResult


class ScenarioPricingService:
    """Prices LLM scenario definitions through the full-reval engine on the live portfolio."""

    def __init__(
        self,
        *,
        root: Path,
        config: dict[str, Any],
        valuation_date: date,
        asset_class: str | None = None,
        with_capital: bool = True,
        var_lookback: int | None = None,
        cov_lookback: int | None = None,
    ):
        from src.pricer.engine import PricingEngine
        from src.pricer.portfolio.portfolio_loader import load_portfolio
        from src.pricer.risk.factor_history import FactorHistory
        from src.pricer.risk.historical_var import FullRevalVarEngine
        from src.pricer.runner import _build_ctx
        from src.scenariogen.catalog import ScenarioCatalog
        from src.scenariogen.config import load_plausibility_config
        from src.scenariogen.plausibility import PlausibilityChecker
        from src.scenariogen.scenario_gen import ScenarioGenerator
        from src.scenariogen.stress import ScenarioStressEngine

        engine = PricingEngine(config, root)
        var_engine = FullRevalVarEngine(engine, config, root)
        self._stress = ScenarioStressEngine(engine, var_engine)
        catalog = ScenarioCatalog.from_config(FactorHistory(root, config), lookback=cov_lookback, project_root=root)
        self._covariance = catalog.covariance
        self._generator = ScenarioGenerator(catalog.covariance)
        self._checker = PlausibilityChecker(catalog.covariance, load_plausibility_config(root))
        self._trades = load_portfolio(asset_class=asset_class)
        self._ctx = _build_ctx(config, valuation_date, "PV")
        self._with_capital = with_capital
        self._var_lookback = var_lookback
        self.valuation_date = valuation_date
        self.snapshot_id = engine.snapshot_id
        self.cov_id = catalog.covariance.cov_id

    @property
    def covariance_factors(self) -> tuple[str, ...]:
        return self._covariance.factors

    @property
    def covariance_dict(self) -> dict[str, Any]:
        return dict(self._covariance.as_dict())

    def price_result(self, definition: ScenarioDefinition) -> ScenarioStressResult:
        """Materialize + fully reprice a definition, returning the raw engine result.

        The raw `ScenarioStressResult` is what the StressReport writer needs (trades, capital,
        plausibility, materialized vector) to surface LLM scenarios in the existing BFF/UI (P4).
        """
        materialized = self._generator.materialize(definition)
        return self._stress.run(
            materialized, self._trades, self._ctx,
            with_capital=self._with_capital, var_lookback=self._var_lookback, checker=self._checker,
        )

    def price(self, definition: ScenarioDefinition) -> PricedScenario:
        result = self.price_result(definition)
        capital = result.capital
        plausibility = result.plausibility
        vector = result.materialized.get("vector", {})
        return PricedScenario(
            name=definition.name,
            total_pnl=result.total_pnl,
            pnl_by_asset_class=dict(result.by_asset_class()),
            capital_delta=(capital.capital_delta if capital else None),
            es_base=(capital.es_base if capital else None),
            es_shocked=(capital.es_shocked if capital else None),
            plausibility_status=(plausibility.status if plausibility else "n/a"),
            vector=dict(vector),
        )
