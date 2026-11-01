"""Hybrid expected-return model for position sizing.

Produces an expected **annual P&L in dollars at the trade's current size** for every
trade — the return signal the aggressive/risky optimizers tilt toward. Two engines feed
one vector (per the sizing mandate):

* **Carry / theta** for fixed income and derivatives — grounded in the book's own
  economics, no market view required:
    - bonds        : (coupon - funding) x face                     [financed carry]
    - IRS (payer)  : (floating - fixed) x notional ~ (funding - fixed) x notional
    - swaptions/cap: -PV / max(T,1)                                 [long-premium theta proxy]
    - equity opts  : delta*(underlier drift) - PV/max(T,1)             [delta view + theta decay]
    - CDS sell     : +running_coupon x notional                    [spread carry earned]
    - CDS buy      : -running_coupon x notional                    [protection premium paid]
    - FX forward   : (funding - foreign_rate) x usd_notional       [rate differential]
    - XCCY swap    : (usd_fixed - foreign_fixed) x usd_notional    [cross-ccy carry]
* **Explicit expected return** for cash equity, where growth/valuation matters:
    - stock        : market_value x (beta x ERP + growth_tilt)

Signs follow the book's P&L: a short's expected return is negative, a bought hedge's
carry is a negative (its cost). Every coefficient (funding, ERP, beta, tilt, foreign rates)
is config-driven (``configs/pricer/sizing.json``) so the assumptions are auditable.

The theta proxy ``-PV/T`` overstates decay for deep in-the-money options (PV carries
intrinsic, which does not decay); it is a documented v1 simplification, flagged in the
model notes, and material only for the AAPL LEAPS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

logger = logging.getLogger("pricer.sizing.expected_returns")

_DAYS_PER_YEAR = 365.25


@dataclass
class ExpectedReturn:
    trade_id: str
    mu_annual: float          # expected annual P&L ($) at current size
    method: str               # "equity_erp" | "bond_carry" | "option_theta" | ...
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"trade_id": self.trade_id, "mu_annual": self.mu_annual,
                "method": self.method, "detail": self.detail}


class ExpectedReturnModel:
    """Compute mu (expected annual P&L at current size) for each raw trade dict."""

    def __init__(self, cfg: dict[str, Any]):
        er = cfg.get("expected_returns", {})
        self.funding = float(er.get("funding_rate", 0.043))
        self.erp = float(er.get("equity_risk_premium", 0.055))
        self.betas: dict[str, float] = er.get("betas", {})
        self.tilts: dict[str, float] = er.get("growth_tilt", {})
        self.foreign_rates: dict[str, float] = er.get("foreign_rates", {})
        self.default_beta = float(er.get("default_beta", 1.0))

    def compute(self, raw_trades: list[dict[str, Any]], pv_by_id: dict[str, float],
                valuation_date: date,
                sens_by_id: dict[str, dict[str, float]] | None = None
                ) -> dict[str, ExpectedReturn]:
        sens_by_id = sens_by_id or {}
        out: dict[str, ExpectedReturn] = {}
        for t in raw_trades:
            tid = t["trade_id"]
            pv = pv_by_id.get(tid, 0.0)
            er = self._one(t, pv, valuation_date, sens_by_id.get(tid, {}))
            if er is not None:
                out[tid] = er
        return out

    def _equity_drift(self, ticker: str) -> float:
        """Expected annual return (fraction) of an equity underlier: beta*ERP + growth tilt."""
        beta = float(self.betas.get(ticker, self.default_beta))
        return beta * self.erp + float(self.tilts.get(ticker, 0.0))

    # -- per-instrument ------------------------------------------------------ #
    def _one(self, t: dict[str, Any], pv: float, vdate: date,
             sens: dict[str, float]) -> ExpectedReturn | None:
        tt = t.get("trade_type", "")
        tid = t["trade_id"]
        p = t.get("params", {})
        notl = float(t.get("notional", 0.0) or 0.0)

        if tt == "stock":
            tkr = p.get("ticker", "")
            drift = self._equity_drift(tkr)
            mu = pv * drift                      # pv signed (short => negative)
            return ExpectedReturn(tid, mu, "equity_erp",
                                  f"{tkr}: MV {pv:,.0f} x drift {drift:.1%}")

        if tt in ("corp_bond", "ust_bond"):
            coupon = float(p.get("coupon", 0.0))
            mu = (coupon - self.funding) * notl
            return ExpectedReturn(tid, mu, "bond_carry",
                                  f"({coupon:.2%} coupon - {self.funding:.2%} funding) x {notl:,.0f}")

        if tt == "ois_swap":
            fixed = float(p.get("fixed_rate", 0.0))
            payer = p.get("direction", "payer") == "payer"
            # payer receives floating (~ funding), pays fixed
            carry = (self.funding - fixed) if payer else (fixed - self.funding)
            mu = carry * notl
            return ExpectedReturn(tid, mu, "swap_carry",
                                  f"{'payer' if payer else 'receiver'} carry "
                                  f"({self.funding:.2%}-{fixed:.2%}) x {notl:,.0f}")

        if tt in ("european_swaption", "bermudan_swaption", "cap"):
            tenor = p.get("option_tenor") or p.get("cap_tenor") or "1Y"
            years = _tenor_years(tenor)
            mu = -pv / max(years, 1.0)                  # long premium decays; cap annual <= premium
            return ExpectedReturn(tid, mu, "option_theta",
                                  f"theta proxy -PV({pv:,.0f})/max({years:.1f}y,1)")

        if tt == "equity_option":
            years = _option_years(p, vdate)
            theta = -pv / max(years, 1.0)               # cap annual decay at the net premium
            # Directional delta view: EQUITY_DELTA is PV per +1% underlier move; expected
            # annual move (in % points) = drift x 100. This is why a bullish call is held.
            tkr = _sens_ticker(sens)
            delta_pnl = 0.0
            if tkr:
                delta_sens = sens.get(f"EQUITY_DELTA:{tkr}", 0.0)
                delta_pnl = delta_sens * self._equity_drift(tkr) * 100.0
            mu = theta + delta_pnl
            return ExpectedReturn(tid, mu, "option_delta_theta",
                                  f"delta view {delta_pnl:,.0f} + theta {theta:,.0f}")

        if tt == "cds":
            coupon = float(p.get("running_coupon", 0.0))
            sell = p.get("protection", "sell") == "sell"
            mu = (coupon * notl) if sell else (-coupon * notl)
            return ExpectedReturn(tid, mu, "cds_carry",
                                  f"{'sell' if sell else 'buy'} protection "
                                  f"{'+' if sell else '-'}{coupon:.2%} x {notl:,.0f}")

        if tt == "fx_forward":
            ccy = p.get("foreign_ccy", "")
            fr = float(self.foreign_rates.get(ccy, 0.0))
            usd = float(p.get("usd_notional", notl))
            long_usd = p.get("direction", "long_usd") == "long_usd"
            carry = (self.funding - fr) if long_usd else (fr - self.funding)
            mu = carry * usd
            return ExpectedReturn(tid, mu, "fx_carry",
                                  f"{'long' if long_usd else 'short'} USD/{ccy} "
                                  f"({self.funding:.2%}-{fr:.2%}) x {usd:,.0f}")

        if tt == "xccy_swap":
            usd_fixed = float(p.get("usd_fixed_rate", 0.0))
            for_fixed = float(p.get("foreign_fixed_rate", 0.0))
            usd = float(p.get("usd_notional", notl))
            long_usd = p.get("direction", "long_usd") == "long_usd"
            carry = (usd_fixed - for_fixed) if long_usd else (for_fixed - usd_fixed)
            mu = carry * usd
            return ExpectedReturn(tid, mu, "xccy_carry",
                                  f"receive USD {usd_fixed:.2%} - pay "
                                  f"{p.get('foreign_ccy','')} {for_fixed:.2%} x {usd:,.0f}")

        logger.warning("expected-return: no model for trade_type %r (%s) -> mu=0", tt, tid)
        return ExpectedReturn(tid, 0.0, "none", f"no model for {tt}")


def _sens_ticker(sens: dict[str, float]) -> str | None:
    """Recover an option's equity underlier from its re-keyed EQUITY_DELTA:<tkr> sensitivity."""
    for k in sens:
        if k.startswith("EQUITY_DELTA:"):
            return k.split(":", 1)[1]
    return None


def _tenor_years(tenor: str) -> float:
    tenor = str(tenor).strip().upper()
    try:
        if tenor.endswith("Y"):
            return float(tenor[:-1])
        if tenor.endswith("M"):
            return float(tenor[:-1]) / 12.0
    except ValueError:
        pass
    return 1.0


def _option_years(params: dict[str, Any], vdate: date) -> float:
    legs = params.get("legs", [])
    expiries: list[date] = []
    for leg in legs:
        try:
            expiries.append(date.fromisoformat(leg["expiry"]))
        except (KeyError, ValueError):
            continue
    if not expiries:
        return 0.25
    # Dominant (longest-dated) leg drives the decay horizon.
    horizon = max(expiries)
    return max((horizon - vdate).days / _DAYS_PER_YEAR, 1.0 / _DAYS_PER_YEAR)
