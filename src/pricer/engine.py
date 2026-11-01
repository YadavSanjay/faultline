"""PricingEngine (P1b): the pricing library, decoupled from the CLI driver.

One engine instance holds the reusable, expensive-to-build state — config, the
immutable market-data snapshot (P0b), and the loader — and exposes a small, stable API:

    price(trade, ctx, run_prov?)            -> PricingResult        # pure, no file I/O
    price_portfolio(trades, ctx, run_prov?) -> list[TradeOutcome]   # fault-isolated

Both the EOD batch runner and the on-demand HTTP service call this same engine, so
batch and intraday can never disagree on the numbers. Persistence (result/summary/
quality files), CLI parsing, and process-level parallelism live in the drivers, not here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .input.common.quality import DataQualityError
from .input.common.trade import Trade
from .input.common.valuation import ValuationContext
from .loader.market_data_loader import MarketDataLoader
from .loader.snapshot import MarketDataSnapshot
from .obs import metrics
from .obs.log import log_context
from .obs.tracing import observe_duration
from .provenance import RunProvenance
from .ql.common.curve_cache import curve_cache
from .ql.common.pricing_result import PricingResult
from .ql.factory import get_pricer

logger = logging.getLogger("pricer.engine")


@dataclass
class TradeOutcome:
    """Per-trade result of a portfolio run: success, blocked, or failed.

    `result` is the priced `PricingResult` when status == "ok", else None; `warnings`
    carries staleness/data warnings on success or the error message on failure/block.
    """

    trade_id: str
    asset_class: str
    trade_type: str
    currency: str
    status: str                       # "ok" | "BLOCKED: ..." | "FAILED: ..."
    result: PricingResult | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class PricingEngine:
    """Stateless-per-call pricing over a fixed market-data snapshot."""

    def __init__(self, config: dict, project_root: Path,
                 snapshot: MarketDataSnapshot | None = None):
        self.config = config
        self.root = project_root
        self.snapshot = snapshot or MarketDataSnapshot.build(config, project_root)
        self.loader = MarketDataLoader(config, project_root, snapshot=self.snapshot)

    @property
    def snapshot_id(self) -> str:
        return self.snapshot.snapshot_id

    def price(self, trade: Trade, ctx: ValuationContext,
              run_prov: RunProvenance | None = None) -> PricingResult:
        """Price one trade and return the provenance-stamped result. Raises on failure."""
        run_prov = run_prov or RunProvenance.capture(self.root, self.config)
        request = trade.market_data_requirements(ctx)
        mds = self.loader.resolve(request, ctx)
        pricer = get_pricer(trade)
        result = pricer.price(trade, mds, ctx)
        result.provenance = run_prov.result_provenance(
            pricer_class=type(pricer).__name__,
            market_data_snapshot_id=getattr(mds, "snapshot_id", None),
        )
        return result

    def price_portfolio(self, trades: list[Trade], ctx: ValuationContext,
                        run_prov: RunProvenance | None = None) -> list[TradeOutcome]:
        """Price many trades under one shared curve cache; isolate per-trade faults."""
        run_prov = run_prov or RunProvenance.capture(self.root, self.config)
        with curve_cache() as cc:
            outcomes = [self._outcome(trade, ctx, run_prov) for trade in trades]
        logger.info("curve cache: %s", cc.stats())
        return outcomes

    def _outcome(self, trade: Trade, ctx: ValuationContext,
                 run_prov: RunProvenance) -> TradeOutcome:
        tid, ac, tt, ccy = trade.trade_id, trade.asset_class.value, trade.trade_type.value, trade.currency
        with log_context(trade_id=tid, asset_class=ac):
            try:
                with observe_duration(metrics.PRICE_LATENCY, span="price", asset_class=ac):
                    result = self.price(trade, ctx, run_prov)
                logger.info("  %s PV = %s %s", tid, result.currency, f"{result.pv:,.2f}")
                metrics.TRADES.labels(asset_class=ac, status="ok").inc()
                for _w in result.warnings:
                    metrics.DATA_QUALITY.labels(severity="warn").inc()
                return TradeOutcome(tid, ac, tt, ccy, "ok", result, list(result.warnings))
            except DataQualityError as exc:
                logger.error("  BLOCKED %s by data-quality gate: %s", tid, exc)
                metrics.TRADES.labels(asset_class=ac, status="blocked").inc()
                metrics.DATA_QUALITY.labels(severity="block").inc()
                return TradeOutcome(tid, ac, tt, ccy, f"BLOCKED: {exc}", None, [str(exc)])
            except Exception as exc:  # one bad trade shouldn't kill the run
                logger.exception("  FAILED to price %s: %s", tid, exc)
                metrics.TRADES.labels(asset_class=ac, status="failed").inc()
                return TradeOutcome(tid, ac, tt, ccy, f"FAILED: {exc}", None, [str(exc)])
