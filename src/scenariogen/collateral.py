"""Cash / collateral line alongside scenario P&L (F2, market-intelligence P4).

A *mode-(iii)* post-processing pass, sibling to ``liquidity`` (F1): what a scenario costs in
P&L is not what it costs in *cash*. What breaks a fund is the margin call, not the mark (design
FINDING 2). This book carries margined derivatives (IR swaps/swaptions, a cap, three CDS, an FX
forward and an XCCY swap) plus a short-ETF cash leg; under a shock they post variation margin and
their initial margin rises with vol, while unmargined positions (bonds, long cash equity) move the
mark but call no cash. So **net cash outflow != net P&L, and the two can have opposite signs**.

Three components, all from data already on disk:

  VM (variation margin)   A MtM loss on a margined trade is posted as cash; a gain is received.
                          Netted across the margined book: ``vm_call = -Σ margined P&L``.
  IM (initial margin)     IM rises with vol under SIMM/CCP models. Proxied as a per-instrument
                          rate on notional, scaled by the scenario's vol stress (from the ``eqvol``
                          / ``ratesvol`` shocks in the materialized vector).
  Funding context         ``SOFR - IORB`` from ``fred_rates.csv`` is a canonical funding-stress
                          gauge (no new ingest); collateral haircuts widen the cash you must post.

The margined/unmargined split is a portfolio tag kept **here** in scenariogen config, not in the
pricing ``portfolio.json`` - so it never touches the pricing snapshot / golden-PV fingerprint.
Degrades gracefully: no config / disabled -> no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.common import storage

from . import factor_keys as fk
from .config import load_collateral_config
from .liquidity import TradeLiqMeta
from .stress import ScenarioStressResult


@dataclass(frozen=True)
class CollateralConfig:
    enabled: bool
    margined_trade_types: frozenset[str]
    margined_overrides: dict[str, bool]
    im_bps_of_notional: dict[str, float]
    vol_stress_floor: float
    vol_stress_cap: float
    eqvol_base: float
    collateral_haircut_bps: float

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> CollateralConfig:
        im = cfg.get("im", {})
        fund = cfg.get("funding", {})
        return cls(
            enabled=bool(cfg.get("enabled", True)),
            margined_trade_types=frozenset(str(t) for t in cfg.get("margined_trade_types", [])),
            margined_overrides={str(k): bool(v) for k, v in cfg.get("margined_overrides", {}).items()},
            im_bps_of_notional={str(k): float(v) for k, v in im.get("bps_of_notional", {}).items()},
            vol_stress_floor=float(im.get("vol_stress_floor", 0.25)),
            vol_stress_cap=float(im.get("vol_stress_cap", 3.0)),
            eqvol_base=float(im.get("eqvol_base", 0.20)),
            collateral_haircut_bps=float(fund.get("collateral_haircut_bps", 0.0)),
        )

    def is_margined(self, meta: TradeLiqMeta) -> bool:
        if meta.trade_id in self.margined_overrides:
            return self.margined_overrides[meta.trade_id]
        return meta.trade_type in self.margined_trade_types


def funding_spread_bps(project_root: Path) -> float | None:
    """Latest ``SOFR - IORB`` from ``fred_rates.csv``, in basis points (the funding-stress gauge).

    FRED quotes both in percent, so the spread in bp is ``(SOFR - IORB) * 100``. ``None`` if the
    file or either column is missing (the line is still emitted, just without funding context).
    """
    loc = storage.join(storage.data_base(project_root), "marketdata", "rates", "fred_rates.csv")
    if not storage.exists(loc):
        return None
    df = storage.read_csv(loc)
    if df.empty or not {"SOFR", "IORB"}.issubset(df.columns):
        return None
    if "date" in df.columns:
        df = df.sort_values("date")
    row = df.dropna(subset=["SOFR", "IORB"]).tail(1)
    if row.empty:
        return None
    return float((row["SOFR"].iloc[0] - row["IORB"].iloc[0]) * 100.0)


def _vol_stress(vector: dict[str, Any], cfg: CollateralConfig) -> float:
    """Scenario vol stress in [floor, cap]: the larger of the relative rates-vol shock and the
    equity-vol shock (additive vol points) expressed relative to a typical vol level."""
    ratesvol = abs(float(vector.get(fk.RATES_VOL_KEY, 0.0) or 0.0))
    eqvol_pts = abs(float(vector.get(fk.EQUITY_VOL_KEY, 0.0) or 0.0))
    eqvol_rel = eqvol_pts / cfg.eqvol_base if cfg.eqvol_base > 0 else 0.0
    stress = max(ratesvol, eqvol_rel)
    return float(min(max(stress, cfg.vol_stress_floor), cfg.vol_stress_cap))


@dataclass
class TradeCashLine:
    trade_id: str
    trade_type: str
    margined: bool
    pnl: float
    vm_flow: float        # cash posted (>0) / received (<0) as variation margin
    im_increase: float    # >= 0

    def as_dict(self) -> dict[str, Any]:
        return {"trade_id": self.trade_id, "trade_type": self.trade_type,
                "margined": self.margined, "pnl": self.pnl, "vm_flow": self.vm_flow,
                "im_increase": self.im_increase}


class CollateralEstimator:
    """Builds the cash/collateral line for one scenario/tier (F2)."""

    def __init__(self, config: CollateralConfig, funding_bps: float | None):
        self.cfg = config
        self.funding_bps = funding_bps

    @classmethod
    def build(cls, project_root: Path, config: dict[str, Any] | None = None) -> CollateralEstimator | None:
        cfg = CollateralConfig.from_dict(config if config is not None else load_collateral_config(project_root))
        if not cfg.enabled:
            return None
        return cls(cfg, funding_spread_bps(project_root))

    def estimate(self, result: ScenarioStressResult, meta: dict[str, TradeLiqMeta],
                 vector: dict[str, Any]) -> dict[str, Any]:
        vol_stress = _vol_stress(vector, self.cfg)
        lines: list[TradeCashLine] = []
        for r in result.results:
            m = meta.get(r.trade_id)
            if m is None:
                continue
            margined = self.cfg.is_margined(m)
            if not margined:
                lines.append(TradeCashLine(r.trade_id, m.trade_type, False, r.pnl, 0.0, 0.0))
                continue
            im_rate = self.cfg.im_bps_of_notional.get(m.trade_type, 0.0) / 1e4
            im_increase = im_rate * abs(m.notional) * vol_stress
            lines.append(TradeCashLine(
                trade_id=r.trade_id, trade_type=m.trade_type, margined=True,
                pnl=r.pnl, vm_flow=-r.pnl, im_increase=im_increase))

        margined_lines = [ln for ln in lines if ln.margined]
        vm_call = sum(ln.vm_flow for ln in margined_lines)       # net VM outflow (can be < 0)
        im_increase = sum(ln.im_increase for ln in margined_lines)
        haircut = max(0.0, vm_call) * self.cfg.collateral_haircut_bps / 1e4
        net_cash_outflow = vm_call + im_increase + haircut
        return {
            "vm_call": vm_call,
            "im_increase": im_increase,
            "collateral_haircut": haircut,
            "net_cash_outflow": net_cash_outflow,
            "total_pnl": result.total_pnl,                       # contrast: mark vs cash
            "margined_pnl": sum(ln.pnl for ln in margined_lines),
            "vol_stress": vol_stress,
            "funding_spread_bps": self.funding_bps,
            "trades": [ln.as_dict() for ln in lines if ln.margined],
        }
