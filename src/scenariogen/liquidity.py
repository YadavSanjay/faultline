"""Liquidity-horizon + bid-ask exit-value adjustment (F1, market-intelligence P4).

A *mode-(iii)* post-processing pass on a materialized ``ScenarioStressResult``: it never
enters the covariance matrix, the plausibility gate, or the pricing snapshot - it reads the
per-trade mid-market P&L the stress engine already produced and re-expresses it as an
*exit-value* estimate. Two effects, both from data already on disk (design FINDING 1):

  Liquidity horizon   Basel/FRTB assigns a liquidation horizon by risk-factor class - 10d for
                      large-cap equity and major rates/FX, up to 60d+ for an illiquid tail like
                      the USD/TWD XCCY swap (#26). A longer horizon lets the market move further
                      against a position being unwound, so the adverse P&L is scaled by
                      ``√(LH / base_horizon)``. The scaler is applied to *losses only*: a longer
                      horizon deepens what you can lose on the way out, it does not enlarge a gain.

  Bid-ask haircut     Crossing the spread to exit is a certain cost. For option legs it comes
                      from the chain ``bid``/``ask`` already downloaded and otherwise discarded
                      (a direct, position-specific liquidity measure); for everything else from a
                      per-instrument half-spread floor in config. Always a cost (reduces P&L).

The horizon table is a desk/governance decision (``configs/scenariogen/liquidity.json`` ->
``signoff``), not a data one - it reprices the meaning of every published scenario, so it is
signed off before it merges. Everything degrades gracefully: no config / disabled -> the pass
is a no-op and the report is byte-identical to before.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.common import storage
from src.pricer.input.common.trade import Trade

from .config import load_liquidity_config
from .stress import ScenarioStressResult

# Instruments whose economic size is their notional, not their (near-zero) PV: the bid-ask
# haircut for a swap/CDS/forward is paid on the risk it carries, not on today's mark. Cash
# instruments (stock, bonds, options) are haircut on |market value| ~ |base_pv|.
_DERIVATIVE_TYPES = frozenset({
    "ois_swap", "cap", "european_swaption", "bermudan_swaption",
    "cds", "fx_forward", "xccy_swap",
})


@dataclass(frozen=True)
class LiquidityConfig:
    enabled: bool
    base_horizon_days: int
    by_trade_type: dict[str, int]
    cds_index_horizon_days: int
    by_foreign_ccy: dict[str, int]
    use_chain_spreads: bool
    max_relative_half_spread: float
    half_spread_bps: dict[str, float]
    signoff: dict[str, Any]

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> LiquidityConfig:
        hz = cfg.get("horizon_days", {})
        ba = cfg.get("bid_ask", {})
        return cls(
            enabled=bool(cfg.get("enabled", True)),
            base_horizon_days=int(cfg.get("base_horizon_days", 10)),
            by_trade_type={str(k): int(v) for k, v in hz.get("by_trade_type", {}).items()},
            cds_index_horizon_days=int(hz.get("cds_index_horizon_days", 20)),
            by_foreign_ccy={str(k).upper(): int(v) for k, v in hz.get("by_foreign_ccy", {}).items()},
            use_chain_spreads=bool(ba.get("use_chain_spreads", True)),
            max_relative_half_spread=float(ba.get("max_relative_half_spread", 0.25)),
            half_spread_bps={str(k): float(v) for k, v in ba.get("half_spread_bps", {}).items()},
            signoff=dict(cfg.get("signoff", {})),
        )


@dataclass(frozen=True)
class TradeLiqMeta:
    """The trade-side facts F1 needs, extracted from the domain object (not the result)."""
    trade_id: str
    trade_type: str
    notional: float
    is_derivative: bool
    option_underlier: str | None   # chain-spread lookup key for equity options
    foreign_ccy: str | None        # fx / xccy currency, for the per-ccy horizon override
    cds_is_index: bool


def trade_liq_meta(trades: list[Trade]) -> dict[str, TradeLiqMeta]:
    """Extract per-trade liquidity metadata from the domain objects via their public fields."""
    out: dict[str, TradeLiqMeta] = {}
    for t in trades:
        ttype = t.trade_type.value
        kind = getattr(t, "kind", None)
        fccy = getattr(t, "foreign_ccy", None)
        out[t.trade_id] = TradeLiqMeta(
            trade_id=t.trade_id,
            trade_type=ttype,
            notional=float(t.notional),
            is_derivative=ttype in _DERIVATIVE_TYPES,
            option_underlier=getattr(t, "underlier", None),
            foreign_ccy=(str(fccy).upper() if fccy is not None else None),
            cds_is_index=(getattr(kind, "value", None) == "index"),
        )
    return out


class ChainSpreadProvider:
    """Per-underlier relative half-spread ``(ask-bid)/mid / 2`` read off the option chains.

    The chains carry ``bid``/``ask``/``mid`` on every contract; only ``iv`` was consumed before
    (design FINDING 1). We take an open-interest-weighted mean of the relative half-spread over
    reasonably liquid, non-expiring contracts (``mid > 0``, ``7 <= dte <= 400``) as the name's
    exit cost. Reads both the priced chains (``chains/``) and the signal-only chains
    (``chains_signals/``) via the dual-mode storage facade.
    """

    def __init__(self, by_name: dict[str, float]):
        self._by_name = by_name

    @classmethod
    def load(cls, project_root: Path, *, max_relative: float) -> ChainSpreadProvider:
        base = storage.join(storage.data_base(project_root), "marketdata", "options")
        by_name: dict[str, float] = {}
        for sub in ("chains", "chains_signals"):
            loc = storage.join(base, sub)
            if not storage.exists(loc):
                continue
            for fp in storage.glob(loc, "*_chains.csv"):
                df = storage.read_csv(fp)
                if df.empty or not {"underlying", "bid", "ask", "mid"}.issubset(df.columns):
                    continue
                name = str(df["underlying"].iloc[0])
                if name in by_name:
                    continue                       # priced chains win over signal chains
                spread = _weighted_half_spread(df, max_relative=max_relative)
                if spread is not None:
                    by_name[name] = spread
        return cls(by_name)

    def half_spread(self, underlier: str | None) -> float | None:
        return self._by_name.get(underlier) if underlier else None


def _weighted_half_spread(df: Any, *, max_relative: float) -> float | None:
    import numpy as np

    d = df.copy()
    for col in ("bid", "ask", "mid", "days_to_expiry", "open_interest"):
        if col in d.columns:
            d[col] = _to_num(d[col])
    d = d[(d["mid"] > 0) & (d["ask"] >= d["bid"])]
    if "days_to_expiry" in d.columns:
        d = d[(d["days_to_expiry"] >= 7) & (d["days_to_expiry"] <= 400)]
    if d.empty:
        return None
    rel = ((d["ask"] - d["bid"]) / d["mid"] / 2.0).clip(lower=0.0, upper=max_relative)
    w = d["open_interest"].fillna(0.0).clip(lower=0.0) if "open_interest" in d.columns else None
    if w is None or float(w.sum()) <= 0.0:
        return float(rel.mean())
    return float(np.average(rel.to_numpy(float), weights=w.to_numpy(float)))


def _to_num(s: Any) -> Any:
    import pandas as pd
    return pd.to_numeric(s, errors="coerce")


@dataclass
class TradeExitValue:
    trade_id: str
    trade_type: str
    horizon_days: int
    horizon_scaler: float
    mid_pnl: float
    horizon_pnl: float          # mid_pnl scaled by the horizon (losses deepened)
    half_spread: float          # relative half-spread applied
    spread_source: str          # "chain" | "config"
    bid_ask_haircut: float      # >= 0, always a cost
    exit_pnl: float             # horizon_pnl - bid_ask_haircut

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id, "trade_type": self.trade_type,
            "horizon_days": self.horizon_days, "horizon_scaler": self.horizon_scaler,
            "mid_pnl": self.mid_pnl, "horizon_pnl": self.horizon_pnl,
            "half_spread": self.half_spread, "spread_source": self.spread_source,
            "bid_ask_haircut": self.bid_ask_haircut, "exit_pnl": self.exit_pnl,
        }


class LiquidityAdjuster:
    """Turns a scenario's mid-market P&L into an exit-value estimate (F1)."""

    def __init__(self, config: LiquidityConfig, spreads: ChainSpreadProvider | None):
        self.cfg = config
        self.spreads = spreads

    @classmethod
    def build(cls, project_root: Path, config: dict[str, Any] | None = None) -> LiquidityAdjuster | None:
        cfg = LiquidityConfig.from_dict(config if config is not None else load_liquidity_config(project_root))
        if not cfg.enabled:
            return None
        spreads = (ChainSpreadProvider.load(project_root, max_relative=cfg.max_relative_half_spread)
                   if cfg.use_chain_spreads else None)
        return cls(cfg, spreads)

    def horizon_days(self, meta: TradeLiqMeta) -> int:
        if meta.foreign_ccy and meta.foreign_ccy in self.cfg.by_foreign_ccy:
            return self.cfg.by_foreign_ccy[meta.foreign_ccy]
        if meta.trade_type == "cds" and meta.cds_is_index:
            return self.cfg.cds_index_horizon_days
        return self.cfg.by_trade_type.get(meta.trade_type, self.cfg.base_horizon_days)

    def _half_spread(self, meta: TradeLiqMeta) -> tuple[float, str]:
        if self.spreads is not None and meta.trade_type == "equity_option":
            chain = self.spreads.half_spread(meta.option_underlier)
            if chain is not None:
                return chain, "chain"
        return self.cfg.half_spread_bps.get(meta.trade_type, 0.0) / 1e4, "config"

    def _exit_value(self, mid_pnl: float, base_pv: float, meta: TradeLiqMeta) -> TradeExitValue:
        lh = self.horizon_days(meta)
        scaler = math.sqrt(lh / self.cfg.base_horizon_days) if self.cfg.base_horizon_days > 0 else 1.0
        horizon_pnl = mid_pnl * scaler if mid_pnl < 0 else mid_pnl   # deepen losses only
        half_spread, source = self._half_spread(meta)
        liq_base = abs(meta.notional) if meta.is_derivative else abs(base_pv)
        haircut = half_spread * liq_base
        return TradeExitValue(
            trade_id=meta.trade_id, trade_type=meta.trade_type, horizon_days=lh,
            horizon_scaler=scaler, mid_pnl=mid_pnl, horizon_pnl=horizon_pnl,
            half_spread=half_spread, spread_source=source, bid_ask_haircut=haircut,
            exit_pnl=horizon_pnl - haircut)

    def adjust(self, result: ScenarioStressResult, meta: dict[str, TradeLiqMeta]) -> dict[str, Any]:
        """Compute per-trade exit values and the aggregate exit-value P&L for one scenario/tier."""
        trades: list[TradeExitValue] = []
        for r in result.results:
            m = meta.get(r.trade_id)
            if m is None:
                continue
            trades.append(self._exit_value(r.pnl, r.base_pv, m))
        mid_total = sum(t.mid_pnl for t in trades)
        exit_total = sum(t.exit_pnl for t in trades)
        horizon_cost = sum(t.horizon_pnl - t.mid_pnl for t in trades)
        bid_ask_cost = sum(t.bid_ask_haircut for t in trades)
        return {
            "mid_pnl": mid_total,
            "exit_pnl": exit_total,
            "liquidity_cost": exit_total - mid_total,          # <= 0, the total exit-value drag
            "horizon_cost": horizon_cost,                      # <= 0, from the LH scaler
            "bid_ask_cost": bid_ask_cost,                      # >= 0 magnitude, a P&L reduction
            "max_horizon_days": max((t.horizon_days for t in trades), default=0),
            "signoff": self.cfg.signoff,
            "trades": [t.as_dict() for t in trades],
        }
