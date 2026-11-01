"""Jump-to-default / incremental default risk add-on (Tier 3.9).

Historical-simulation VaR captures credit-*spread* moves but not the discrete loss when an
issuer actually defaults — a gap for a book of corporate bonds and single-name CDS. This module
computes the instantaneous jump-to-default (JTD) P&L per issuer: on an issuer's default a long
bond loses its loss-given-default (LGD = 1 - recovery) on face, a bought-protection CDS gains
LGD on notional, and a sold-protection CDS loses it. Netting is within an issuer only — defaults
are idiosyncratic, so a gain on one name does not offset a loss on another; the incremental
default charge (IDR, a simplified FRTB-DRC) is the sum of the per-issuer net *losses*.

Index CDS default risk is diversified across ~125 constituents (a single default hits 1/n of
notional), so index positions are reported separately and excluded from the single-name IDR.
Credit names are tagged IG/HY from ``risk.jtd.buckets`` so the add-on doubles as the issuer /
rating-bucket credit split.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..input.bonds.instruments import CorporateBondTrade
from ..input.common.enums import CdsKind, ProtectionSide
from ..input.common.trade import Trade
from ..input.credit.instruments import CreditDefaultSwapTrade


@dataclass
class IssuerJtd:
    issuer: str
    bucket: str                  # IG | HY | index | unrated
    jtd: float                   # signed P&L on instantaneous default (- = net loss)
    gross_notional: float        # sum of |notional| at default risk to this issuer
    lgd: float                   # loss-given-default used (1 - recovery)
    n_positions: int

    def as_dict(self) -> dict[str, Any]:
        return {"issuer": self.issuer, "bucket": self.bucket, "jtd": self.jtd,
                "gross_notional": self.gross_notional, "lgd": self.lgd,
                "n_positions": self.n_positions}


@dataclass
class JtdReport:
    valuation_date: str
    issuers: list[IssuerJtd] = field(default_factory=list)
    index_positions: list[IssuerJtd] = field(default_factory=list)   # diversified, excluded from IDR

    @property
    def idr(self) -> float:
        """Incremental default charge: sum of per-issuer net losses (no cross-name netting)."""
        return sum(-i.jtd for i in self.issuers if i.jtd < 0.0)

    @property
    def total_net_jtd(self) -> float:
        return sum(i.jtd for i in self.issuers)

    @property
    def worst(self) -> IssuerJtd | None:
        losers = [i for i in self.issuers if i.jtd < 0.0]
        return min(losers, key=lambda i: i.jtd) if losers else None

    def by_bucket(self) -> dict[str, float]:
        agg: dict[str, float] = {}
        for i in self.issuers:
            agg[i.bucket] = agg.get(i.bucket, 0.0) + i.jtd
        return agg

    def to_dict(self) -> dict[str, Any]:
        worst = self.worst
        return {
            "valuation_date": self.valuation_date,
            "idr": self.idr,
            "total_net_jtd": self.total_net_jtd,
            "worst_issuer": worst.issuer if worst else None,
            "worst_jtd": worst.jtd if worst else 0.0,
            "jtd_by_bucket": self.by_bucket(),
            "issuers": [i.as_dict() for i in self.issuers],
            "index_positions": [i.as_dict() for i in self.index_positions],
        }


class JtdEngine:
    def __init__(self, config: dict[str, Any]):
        risk_cfg = config.get("risk", {}).get("jtd", {}) or {}
        self.recovery_bond = float(risk_cfg.get("recovery_bond",
                                   config.get("bonds", {}).get("credit_spread", {}).get("recovery", 0.40)))
        self.buckets: dict[str, str] = risk_cfg.get("buckets", {}) or {}
        self.index_size = int(risk_cfg.get("index_size", 125))   # constituents per CDX series

    def compute(self, trades: list[Trade], valuation_date: str) -> JtdReport:
        # issuer -> [signed jtd, gross_notional, lgd(last), n]
        single: dict[str, list[float]] = {}
        index: list[IssuerJtd] = []
        for trade in trades:
            leg = self._trade_jtd(trade)
            if leg is None:
                continue
            issuer, jtd, gross, lgd, is_index = leg
            if is_index:
                index.append(IssuerJtd(issuer, "index", jtd, gross, lgd, 1))
                continue
            acc = single.setdefault(issuer, [0.0, 0.0, lgd, 0.0])
            acc[0] += jtd
            acc[1] += gross
            acc[2] = lgd
            acc[3] += 1

        issuers = [
            IssuerJtd(name, self.buckets.get(name, "IG"), v[0], v[1], v[2], int(v[3]))
            for name, v in single.items()
        ]
        issuers.sort(key=lambda i: i.jtd)   # worst loss first
        return JtdReport(valuation_date, issuers, index)

    def _trade_jtd(self, trade: Trade) -> tuple[str, float, float, float, bool] | None:
        """Return (issuer, signed_jtd, gross_notional, lgd, is_index) or None if no default risk."""
        notional = abs(float(getattr(trade, "notional", 0.0) or 0.0))
        if isinstance(trade, CorporateBondTrade):
            lgd = 1.0 - self.recovery_bond
            return (trade.issuer or trade.trade_id, -lgd * notional, notional, lgd, False)
        if isinstance(trade, CreditDefaultSwapTrade):
            lgd = 1.0 - float(trade.recovery)
            # buy protection gains on default (+), sell protection loses (-)
            sign = 1.0 if trade.protection == ProtectionSide.BUY else -1.0
            is_index = trade.kind == CdsKind.INDEX
            # A single constituent default hits ~1/n of an index's notional (diversified); a
            # single name hits the full notional.
            jtd = sign * lgd * notional / (self.index_size if is_index else 1)
            return (trade.reference, jtd, notional, lgd, is_index)
        return None
