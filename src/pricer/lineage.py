"""Calculation-lineage "explain" pass (ENG-1, supports the UI Pricing lineage page).

Re-derives, for a priced trade, the intermediate values the pricers compute internally
but don't persist: the curve points used (zero / discount-factor / forward rates), the
cashflow ladder, per-leg option detail, the CDS survival curve, FX covered-interest-parity,
etc. It reuses the same builders the pricers use so the explain matches the price -- it does
not touch the pricers themselves (no regression risk to golden/IPV).

Covers all priced instruments: ois_swap, stock, corp_bond, ust_bond, equity_option, cds,
fx_forward, xccy_swap, european_swaption, bermudan_swaption, cap. The ``final`` block is
taken from the actual priced result, so even if an instrument's intermediate extraction is
imperfect, the lineage still shows the value, measures, inputs, and sensitivity.

Output ``intermediates`` use a generic, FE-renderable schema:
  curves      {name: {tenors, par_rates, zero_rates, discount_factors, forward_rates}}
  cashflows   [{leg, pay_date, accrual, rate, amount, discount_factor, pv}]
  tables      [{title, columns, rows}]   -- per-leg / per-point detail
  computation [{step, value}]            -- ordered scalar derivation
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

import QuantLib as ql

from .engine import PricingEngine
from .input.common.enums import CurvePurpose, MarketDataKind
from .input.common.trade import Trade
from .input.common.valuation import ValuationContext
from .ql.bonds.bond_pricer import build_fixed_rate_bond
from .ql.common import conventions as cv
from .ql.common.base_pricer import BasePricer
from .ql.common.curve_builder import (
    build_ois_curve,
    build_treasury_curve,
    build_zero_spreaded_curve,
    build_zero_spreaded_curve_ts,
)
from .ql.common.curve_cache import curve_cache
from .ql.common.pricing_result import PricingResult
from .ql.credit.cds_pricer import calibrate_flat_hazard, calibrate_piecewise_hazard
from .ql.factory import get_pricer
from .ql.rates.swap_pricer import build_ois_swap
from .risk import bumps

logger = logging.getLogger("pricer.lineage")

_DC = ql.Actual365Fixed()
_BP = 1e-4
_RECON_TOL = 1.0  # USD


def supported(trade_type: str) -> bool:
    return trade_type in _DISPATCH


def _safe(fn: Callable[..., Any]) -> Any:
    try:
        return fn()
    except Exception:  # a single intermediate failing must not sink the explain
        return None


def _iso(d: ql.Date) -> str:
    return f"{d.year():04d}-{d.month():02d}-{d.dayOfMonth():02d}"


def _link(handle: ql.YieldTermStructureHandle) -> Any:
    return _safe(lambda: handle.currentLink()) or handle


def _pv(trade: Trade, mds: Any, ctx: ValuationContext) -> float:
    return float(get_pricer(trade).price(trade, mds, ctx).pv)


def _final(res: PricingResult, reconciliation: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"pv": float(res.pv), "leg_pvs": dict(res.leg_pvs),
            "reconciliation": reconciliation or [], "measures": dict(res.measures)}


# --------------------------------------------------------------------------- #
# Shared extractors
# --------------------------------------------------------------------------- #
def _curve_points(handle: ql.YieldTermStructureHandle, as_of: date,
                  tenors: tuple[str, ...], par_rates: tuple[float, ...]) -> dict[str, Any]:
    ts = _link(handle)
    today = cv.to_ql_date(as_of)
    nodes: list[ql.Date | None] = []
    zero: list[float | None] = []
    dfs: list[float | None] = []
    for tenor in tenors:
        nd = _safe(lambda t=tenor: today + cv.to_ql_period(t))
        nodes.append(nd)
        zero.append(_safe(lambda n=nd: ts.zeroRate(n, _DC, ql.Continuous).rate()) if nd else None)
        dfs.append(_safe(lambda n=nd: ts.discount(n)) if nd else None)
    fwd: list[float | None] = [None]
    for i in range(1, len(nodes)):
        d1, d2 = nodes[i - 1], nodes[i]
        fwd.append(
            _safe(lambda a=d1, b=d2: ts.forwardRate(a, b, _DC, ql.Continuous).rate())
            if d1 and d2 else None
        )
    return {"tenors": list(tenors), "par_rates": [float(r) for r in par_rates],
            "zero_rates": zero, "discount_factors": dfs, "forward_rates": fwd,
            "tenors_used": list(tenors)}


def _flow_rows(cfs: Any, leg_name: str, handle: ql.YieldTermStructureHandle,
               today: ql.Date) -> list[dict[str, Any]]:
    ts = _link(handle)
    rows: list[dict[str, Any]] = []
    for cf in cfs or []:
        d = cf.date()
        if d <= today:                                  # only future cashflows
            continue
        amount = _safe(cf.amount)
        df = _safe(lambda dd=d: ts.discount(dd))
        cp = _safe(lambda c=cf: ql.as_coupon(c))
        row: dict[str, Any] = {
            "leg": leg_name, "pay_date": _iso(d), "amount": amount,
            "discount_factor": df, "pv": (amount * df) if (amount is not None and df is not None) else None,
        }
        if cp is not None:
            row["accrual"] = _safe(cp.accrualPeriod)
            row["rate"] = _safe(cp.rate)
            row["notional"] = _safe(cp.nominal)
        rows.append(row)
    return rows


def _market_inputs(trade: Trade, ctx: ValuationContext) -> list[dict[str, Any]]:
    reqs = trade.market_data_requirements(ctx).requirements
    return [
        {
            "kind": getattr(getattr(r, "kind", None), "value", str(getattr(r, "kind", ""))),
            "currency": getattr(r, "currency", None),
            "purpose": getattr(getattr(r, "purpose", None), "value", None),
            "index": getattr(r, "index_name", None),
            "tenor": getattr(r, "tenor", None),
            "key": _safe(lambda rr=r: rr.key),
        }
        for r in reqs
    ]


def _conventions(sched: Any) -> dict[str, Any]:
    if sched is None:
        return {}
    return {
        "day_count": getattr(getattr(sched, "day_count", None), "value", str(getattr(sched, "day_count", ""))),
        "frequency": getattr(getattr(sched, "frequency", None), "value", str(getattr(sched, "frequency", ""))),
        "calendar": getattr(getattr(sched, "calendar", None), "value", str(getattr(sched, "calendar", ""))),
        "bdc": getattr(getattr(sched, "business_day_convention", None), "value",
                       str(getattr(sched, "business_day_convention", ""))),
    }


# --------------------------------------------------------------------------- #
# Per-instrument explains  (trade, ctx, mds, res) -> {final, intermediates, inputs}
# --------------------------------------------------------------------------- #
def _explain_swap(trade: Any, ctx: ValuationContext, mds: Any, res: PricingResult) -> dict[str, Any]:
    discount, _forecast, disc = BasePricer._resolve_ois_curves(mds, trade.currency, trade.index.name)
    swap = build_ois_swap(trade, discount, _forecast)
    fixed_pv, float_pv = float(swap.legNPV(0)), float(swap.legNPV(1))
    today = cv.to_ql_date(ctx.valuation_date)
    cashflows = _flow_rows(_safe(lambda: swap.leg(0)), "fixed", discount, today) + \
        _flow_rows(_safe(lambda: swap.leg(1)), "float", discount, today)
    return {
        "final": _final(res, [{"expr": "fixed + float", "value": float(res.pv),
                               "ok": abs(float(res.pv) - (fixed_pv + float_pv)) < _RECON_TOL}]),
        "intermediates": {
            "curves": {trade.index.name: _curve_points(discount, disc.as_of_date, disc.tenors, disc.par_rates)},
            "cashflows": cashflows,
        },
        "inputs": {"market_data": _market_inputs(trade, ctx),
                   "static": {"notional": trade.notional, "direction": getattr(trade.direction, "value", None),
                              "fixed_rate": trade.fixed_rate, "swap_tenor": trade.swap_tenor,
                              "index": trade.index.name,
                              "conventions": _conventions(getattr(trade, "fixed_schedule", None))},
                   "curve_as_of": disc.as_of_date.isoformat(), "curve_source": disc.source},
    }


def _explain_stock(trade: Any, ctx: ValuationContext, mds: Any, res: PricingResult) -> dict[str, Any]:
    spot = mds.equity_spot(trade.currency, trade.ticker)
    qty = float(trade.quantity)
    pv = float(res.pv)
    return {
        "final": _final(res, [{"expr": "quantity x spot", "value": pv,
                               "ok": abs(pv - qty * spot.spot) < _RECON_TOL}]),
        "intermediates": {"curves": {}, "cashflows": [], "computation": [
            {"step": "quantity", "value": qty}, {"step": "spot", "value": spot.spot},
            {"step": "value = quantity x spot", "value": pv}]},
        "inputs": {"market_data": [{"kind": _ek(MarketDataKind, "EQUITY_SPOT", "equity_spot"),
                                    "ticker": spot.ticker, "spot": spot.spot,
                                    "as_of_date": spot.as_of_date.isoformat(), "source": spot.source,
                                    "dividend_yield": spot.dividend_yield}],
                   "static": {"quantity": qty, "ticker": trade.ticker, "currency": trade.currency}},
    }


def _bond_discount(trade: Any, mds: Any) -> tuple[ql.YieldTermStructureHandle, dict[str, Any], dict[str, Any]]:
    """(discount handle, curve-points base, extra computation/inputs)."""
    if trade.trade_type.value == "corp_bond":
        sofr_data = mds.curve(trade.currency, CurvePurpose.DISCOUNT, trade.index.name)
        sofr = build_ois_curve(sofr_data)
        cs = mds.credit_spread(trade.currency, trade.issuer)
        use_curve = len(cs.curve_points) >= 2
        if use_curve:
            risky = build_zero_spreaded_curve_ts(sofr, cs.curve_points, sofr.currentLink().referenceDate())
            disc_step = f"SpreadedLinearZeroInterpolatedTermStructure ({len(cs.curve_points)} tenors)"
        else:
            risky = build_zero_spreaded_curve(sofr, cs.spread)
            disc_step = "ZeroSpreadedTermStructure"
        credit_curve = [{"tenor": f"{int(t)}Y", "z_spread_bp": round(s * 1e4, 1)} for t, s in cs.curve_points]
        curve = _curve_points(sofr, sofr_data.as_of_date, sofr_data.tenors, sofr_data.par_rates)
        return risky, {"name": trade.index.name, "points": curve, "as_of": sofr_data.as_of_date.isoformat()}, {
            "computation": [{"step": "base SOFR curve", "value": trade.index.name},
                            {"step": "issuer 5Y Z-spread (bp)", "value": round(cs.spread * 1e4, 1)},
                            {"step": "discount = SOFR + issuer Z-spread", "value": disc_step}],
            "market_extra": [{"kind": "credit_spread", "index": trade.issuer, "spread_bp": round(cs.spread * 1e4, 1),
                              "recovery": cs.recovery, "source": cs.source}],
            "credit_curve": credit_curve,
        }
    tdata = mds.treasury_curve(trade.currency)
    handle = build_treasury_curve(tdata, settlement_days=trade.settlement_days, calendar_code=trade.calendar)
    curve = _curve_points(handle, tdata.as_of_date, tdata.tenors, tdata.par_yields)
    return handle, {"name": "UST par curve", "points": curve, "as_of": tdata.as_of_date.isoformat()}, {
        "computation": [{"step": "UST par-curve bootstrap", "value": "treasury"}], "market_extra": []}


def _explain_bond(trade: Any, ctx: ValuationContext, mds: Any, res: PricingResult) -> dict[str, Any]:
    discount, curve_base, extra = _bond_discount(trade, mds)
    bond = build_fixed_rate_bond(trade)
    today = cv.to_ql_date(ctx.valuation_date)
    cashflows = _flow_rows(_safe(bond.cashflows), "bond", discount, today)
    flow_sum = sum(r["pv"] for r in cashflows if r.get("pv") is not None)
    inter: dict[str, Any] = {"curves": {curve_base["name"]: curve_base["points"]}, "cashflows": cashflows,
                             "computation": extra["computation"]}
    credit_curve = extra.get("credit_curve") or []
    if credit_curve:
        inter["credit_spread_curve"] = credit_curve
        inter["tables"] = [{"title": "Issuer credit curve (Z-spread over SOFR, per tenor)",
                            "columns": ["tenor", "z_spread_bp"], "rows": credit_curve}]
    return {
        "final": _final(res, [{"expr": "sum of discounted cashflows", "value": flow_sum,
                               "ok": abs(flow_sum - float(res.pv)) < max(_RECON_TOL, abs(float(res.pv)) * 1e-4)}]),
        "intermediates": inter,
        "inputs": {"market_data": _market_inputs(trade, ctx) + extra["market_extra"],
                   "static": {"notional": trade.notional, "coupon": trade.coupon,
                              "maturity": trade.maturity_date.isoformat(), "issuer": getattr(trade, "issuer", None),
                              "cusip": getattr(trade, "cusip", None),
                              "conventions": {"day_count": trade.day_count.value, "frequency": getattr(
                                  trade.coupon_frequency, "value", str(trade.coupon_frequency))}},
                   "curve_as_of": curve_base["as_of"]},
    }


def _vol_surface_grid(eq: Any, trade: Any, ctx: ValuationContext) -> dict[str, Any] | None:
    """A display grid of the chain's implied-vol surface (moneyness x expiry, IV%) for the
    Explain heatmap, with the trade's leg points marked. Shows the skew (down-strikes richer)
    and term structure each leg is priced off."""
    surf = getattr(eq, "vol_surface", None)
    if surf is None or not surf.days_to_expiry:
        return None
    days_all = sorted({int(d) for d in surf.days_to_expiry if int(d) >= 7})
    if len(days_all) < 2:
        return None
    # up to 7 expiries, evenly sampled across the available range
    n = min(7, len(days_all))
    idx = sorted({round(k * (len(days_all) - 1) / (n - 1)) for k in range(n)})
    days = [days_all[i] for i in idx]
    moneyness = [0.80, 0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15, 1.20]
    strikes = sorted({round(m * eq.spot, 2) for m in moneyness} | {round(leg.strike, 2) for leg in trade.legs})
    ivs = [[round(surf.implied_vol(d / 365.0, k) * 100.0, 2) for d in days] for k in strikes]

    def leg_days(leg: Any) -> int:
        return max(int((leg.expiry_date - ctx.valuation_date).days), 1)

    leg_points = [{"strike": round(leg.strike, 2), "expiry_days": leg_days(leg),
                   "iv_pct": round(surf.implied_vol(leg_days(leg) / 365.0, leg.strike) * 100.0, 2)}
                  for leg in trade.legs]
    return {"underlier": trade.underlier, "spot": round(float(eq.spot), 2),
            "expiries_days": days, "strikes": strikes, "iv_pct": ivs, "leg_points": leg_points}


def _explain_option(trade: Any, ctx: ValuationContext, mds: Any, res: PricingResult) -> dict[str, Any]:
    eq = _safe(lambda: mds.equity_market(trade.currency, trade.underlier))
    rows: list[dict[str, Any]] = []
    for i, leg in enumerate(trade.legs):
        tag = f"leg{i}_{leg.right.value}_{leg.strike:g}"
        rows.append({
            "leg": i, "right": leg.right.value, "strike": leg.strike,
            "expiry": leg.expiry_date.isoformat(),
            "style": getattr(leg.exercise_style, "value", str(leg.exercise_style)),
            "quantity": leg.quantity, "iv": res.measures.get(f"vol_{tag}"),
            "leg_pv": res.leg_pvs.get(tag),
        })
    leg_sum = sum(r["leg_pv"] for r in rows if r.get("leg_pv") is not None)
    comp = []
    div_rows: list[dict[str, Any]] = []
    vol_surface = _safe(lambda: _vol_surface_grid(eq, trade, ctx)) if eq is not None else None
    if eq is not None:
        comp = [{"step": "spot (put-call parity from chain)", "value": eq.spot},
                {"step": "dividend yield", "value": eq.dividend_yield}]
        n_disc = int(res.measures.get("discrete_dividend_legs", 0) or 0)
        if n_disc:
            comp.append({"step": "American div treatment", "value": "discrete (forward-preserving)"})
        # show the projected ex-dividend schedule within the longest leg's life
        last_expiry = max((leg.expiry_date for leg in trade.legs), default=None)
        for d, amt in getattr(eq, "dividends", ()):
            if last_expiry is not None and ctx.valuation_date < d <= last_expiry:
                div_rows.append({"ex_date": d.isoformat(), "amount": round(float(amt), 4)})
    return {
        "final": _final(res, [{"expr": "sum of leg PVs", "value": leg_sum,
                               "ok": abs(leg_sum - float(res.pv)) < max(_RECON_TOL, abs(float(res.pv)) * 1e-4)}]),
        "intermediates": {"curves": {}, "cashflows": [], "computation": comp, "vol_surface": vol_surface, "tables": [{
            "title": "Option legs (Black-Scholes per leg, skew-aware vol from the chain)",
            "columns": ["leg", "right", "strike", "expiry", "style", "quantity", "iv", "leg_pv"],
            "rows": rows}] + ([{
            "title": "Projected discrete ex-dividends (American legs, forward-preserving)",
            "columns": ["ex_date", "amount"], "rows": div_rows}] if div_rows else [])},
        "inputs": {"market_data": _market_inputs(trade, ctx) + ([{
            "kind": "equity_market", "ticker": trade.underlier, "spot": eq.spot,
            "dividend_yield": eq.dividend_yield, "source": eq.source}] if eq is not None else []),
            "static": {"underlier": trade.underlier, "structure": getattr(trade, "structure", None),
                       "n_legs": len(trade.legs), "contract_multiplier": getattr(trade, "contract_multiplier", None)}},
    }


def _cds_schedule(today: ql.Date, tenor: str) -> ql.Schedule:
    maturity = ql.cdsMaturity(today, ql.Period(tenor), ql.DateGeneration.CDS2015)
    return ql.MakeSchedule(today, maturity, ql.Period(ql.Quarterly), ql.Quarterly,
                           ql.WeekendsOnly(), ql.Following, ql.Unadjusted, ql.DateGeneration.CDS2015)


def _explain_cds(trade: Any, ctx: ValuationContext, mds: Any, res: PricingResult) -> dict[str, Any]:
    survival: list[dict[str, Any]] = []
    comp: list[dict[str, Any]] = []
    hazard_curve: list[dict[str, Any]] = []
    method = "flat"
    cs = _safe(lambda: mds.cds_spread(trade.currency, trade.reference))
    if cs is not None:
        discount = build_ois_curve(mds.curve(trade.currency, CurvePurpose.DISCOUNT, trade.index.name),
                                   interpolation="flatforward")
        recovery = cs.recovery if cs.recovery is not None else trade.recovery
        today = cv.to_ql_date(ctx.valuation_date)
        schedule = _cds_schedule(today, trade.tenor)
        points = list(cs.curve_points)
        if len(points) >= 2:
            # A2: piecewise hazard bootstrapped from the issuer's par-spread term structure
            method = "piecewise"
            prob, nodes = calibrate_piecewise_hazard(points, recovery, discount, today)
            comp = [{"step": "calibrated tenors", "value": len(nodes)},
                    {"step": "recovery", "value": recovery},
                    {"step": "5Y par spread (bp)", "value": round(cs.spread * 1e4, 1)}]
            hazard_curve = [{"tenor": n["tenor"], "par_spread_bp": round(n["spread"] * 1e4, 1),
                             "forward_hazard_bp": round(n["hazard"] * 1e4, 1),
                             "survival_prob": round(float(n["survival"]), 5)} for n in nodes]
        else:
            prob, hazard = calibrate_flat_hazard(cs.spread, recovery, discount, today, schedule)
            comp = [{"step": "quoted 5Y par spread (bp)", "value": round(cs.spread * 1e4, 1)},
                    {"step": "recovery", "value": recovery},
                    {"step": "calibrated flat hazard (bp)", "value": round(hazard * 1e4, 1)}]
        for y in (1, 2, 3, 4, 5):
            nd = _safe(lambda yy=y: today + ql.Period(yy, ql.Years))
            sp = _safe(lambda n=nd: prob.survivalProbability(n)) if nd else None
            if sp is not None:
                survival.append({"tenor": f"{y}Y", "survival_prob": round(float(sp), 5),
                                 "default_prob": round(1 - float(sp), 5)})
    tables = []
    if hazard_curve:
        tables.append({"title": "Issuer hazard curve (bootstrapped piecewise-flat, per tenor)",
                       "columns": ["tenor", "par_spread_bp", "forward_hazard_bp", "survival_prob"],
                       "rows": hazard_curve})
    if survival:
        title = ("Survival curve (from the bootstrapped hazard term structure)" if method == "piecewise"
                 else "Survival curve (flat hazard calibrated to par)")
        tables.append({"title": title,
                       "columns": ["tenor", "survival_prob", "default_prob"], "rows": survival})
    return {
        "final": _final(res),
        "intermediates": {"curves": {}, "cashflows": [], "computation": comp, "tables": tables,
                          "hazard_curve": hazard_curve},
        "inputs": {"market_data": _market_inputs(trade, ctx),
                   "static": {"reference": trade.reference, "tenor": trade.tenor,
                              "protection": getattr(trade.protection, "value", str(trade.protection)),
                              "running_coupon_bp": round(trade.running_coupon * 1e4, 1),
                              "notional": trade.notional, "recovery": trade.recovery,
                              "hazard_method": method}},
    }


def _explain_fx(trade: Any, ctx: ValuationContext, mds: Any, res: PricingResult) -> dict[str, Any]:
    comp: list[dict[str, Any]] = []
    fx = _safe(lambda: mds.fx(trade.foreign_ccy))
    if fx is not None:
        usd = _link(build_ois_curve(mds.curve(trade.currency, CurvePurpose.DISCOUNT, trade.index.name)))
        today = cv.to_ql_date(ctx.valuation_date)
        mat = cv.to_ql_date(trade.maturity_date)
        df_usd = _safe(lambda: usd.discount(mat))
        foreign = ql.FlatForward(today, fx.foreign_rate, ql.Actual365Fixed())
        df_for = _safe(lambda: foreign.discount(mat))
        comp = [{"step": "spot (foreign per USD)", "value": fx.spot},
                {"step": "USD discount factor", "value": round(df_usd, 6) if df_usd else None},
                {"step": "foreign discount factor", "value": round(df_for, 6) if df_for else None},
                {"step": "fair forward = spot x DF_usd / DF_for",
                 "value": round(fx.spot * df_usd / df_for, 4) if (df_usd and df_for) else None}]
    return {
        "final": _final(res),
        "intermediates": {"curves": {}, "cashflows": [], "computation": comp},
        "inputs": {"market_data": _market_inputs(trade, ctx) + ([{
            "kind": "fx", "index": f"USD/{trade.foreign_ccy}", "spot": fx.spot,
            "foreign_rate": fx.foreign_rate, "source": fx.source}] if fx is not None else []),
            "static": {k: getattr(trade, k, None) for k in
                       ("strike", "usd_notional", "foreign_notional", "contract_fx", "direction", "maturity_date")}},
    }


def _explain_vol(trade: Any, ctx: ValuationContext, mds: Any, res: PricingResult) -> dict[str, Any]:
    """Swaptions + caps — Bachelier (normal vol) options; surface the key inputs."""
    m = res.measures
    comp = [{"step": "ATM / strike rate", "value": round(float(m.get("atm_or_strike", 0)) * 100, 4)},
            {"step": "normal vol at option expiry (bp)", "value": m.get("normal_vol_bp")}]
    if "underlying_fair_rate" in m:
        comp.insert(1, {"step": "underlying swap fair rate (%)",
                        "value": round(float(m["underlying_fair_rate"]) * 100, 4)})
    if "hw_mean_reversion" in m:
        comp.append({"step": "Hull-White mean reversion a", "value": round(float(m["hw_mean_reversion"]), 4)})
        comp.append({"step": "Hull-White sigma", "value": round(float(m["hw_sigma"]), 5)})
    if "vega_1bp" in m:
        comp.append({"step": "vega (per +1bp vol)", "value": round(float(m["vega_1bp"]), 2)})
    # C1: show the expiry-dependent normal-vol term structure the option is priced off.
    tenor = getattr(trade, "option_tenor", None) or getattr(trade, "cap_tenor", None)
    vol_data = _safe(lambda: mds.normal_vol(trade.currency, tenor))
    tables = []
    if vol_data is not None and getattr(vol_data, "term_structure", None):
        rows = [{"expiry": (f"{t:g}Y" if t >= 1 else f"{round(t * 12)}M"),
                 "normal_vol_bp": round(v * 1e4, 1)} for t, v in vol_data.term_structure]
        tables.append({"title": "Normal-vol term structure (MOVE-anchored, stylized shape)",
                       "columns": ["expiry", "normal_vol_bp"], "rows": rows})
    return {
        "final": _final(res),
        "intermediates": {"curves": {}, "cashflows": [], "computation": comp, "tables": tables},
        "inputs": {"market_data": _market_inputs(trade, ctx),
                   "static": {k: getattr(trade, k, None) for k in
                              ("notional", "option_tenor", "swap_tenor", "cap_tenor", "strike", "direction")
                              if getattr(trade, k, None) is not None}},
    }


# --------------------------------------------------------------------------- #
# Sensitivity bump-and-revalue (RK-B3a) — headline factor per instrument
# --------------------------------------------------------------------------- #
_SENS: dict[str, tuple[str, str, Callable[[float], Any], float]] = {
    "ois_swap": ("IR_PARALLEL", "+1bp parallel", lambda s: bumps.shift_ir(s), _BP),
    "corp_bond": ("IR_PARALLEL", "+1bp parallel", lambda s: bumps.shift_ir(s), _BP),
    "ust_bond": ("IR_PARALLEL", "+1bp parallel", lambda s: bumps.shift_ir(s), _BP),
    "stock": ("EQUITY_DELTA", "+1% underlier", lambda s: bumps.scale_equity(s), 0.01),
    "equity_option": ("EQUITY_DELTA", "+1% underlier", lambda s: bumps.scale_equity(s), 0.01),
    "cds": ("CS01", "+1bp credit spread", lambda s: bumps.shift_credit(s), _BP),
    "fx_forward": ("FX_DELTA", "+1% FX spot", lambda s: bumps.scale_fx(s), 0.01),
    "xccy_swap": ("FX_DELTA", "+1% FX spot", lambda s: bumps.scale_fx(s), 0.01),
    "european_swaption": ("VEGA_RATES", "+1bp normal vol", lambda s: bumps.shift_rates_vol(s), _BP),
    "bermudan_swaption": ("VEGA_RATES", "+1bp normal vol", lambda s: bumps.shift_rates_vol(s), _BP),
    "cap": ("VEGA_RATES", "+1bp normal vol", lambda s: bumps.shift_rates_vol(s), _BP),
}


def _sensitivity_lineage(trade: Trade, ctx: ValuationContext, mds: Any) -> list[dict[str, Any]]:
    spec = _SENS.get(trade.trade_type.value)
    if spec is None:
        return []
    factor, unit, tf, mag = spec
    base = _safe(lambda: _pv(trade, mds, ctx))
    up = _safe(lambda: _pv(trade, bumps.bumped(mds, tf(mag)), ctx))
    dn = _safe(lambda: _pv(trade, bumps.bumped(mds, tf(-mag)), ctx))
    if base is None or up is None or dn is None:
        return []
    return [{"factor": factor, "base_pv": base, "up_pv": up, "down_pv": dn,
             "value": (up - dn) / 2.0, "bump_unit": unit, "method": "central_bump_revalue"}]


def _ek(enum: Any, name: str, fallback: str) -> str:
    member = getattr(enum, name, None)
    return getattr(member, "value", fallback) if member is not None else fallback


_DISPATCH: dict[str, Callable[[Any, ValuationContext, Any, PricingResult], dict[str, Any]]] = {
    "ois_swap": _explain_swap,
    "stock": _explain_stock,
    "corp_bond": _explain_bond,
    "ust_bond": _explain_bond,
    "equity_option": _explain_option,
    "cds": _explain_cds,
    "fx_forward": _explain_fx,
    "xccy_swap": _explain_fx,
    "european_swaption": _explain_vol,
    "bermudan_swaption": _explain_vol,
    "cap": _explain_vol,
}


def build_explain(engine: PricingEngine, trade: Trade, ctx: ValuationContext) -> dict[str, Any] | None:
    """Build the lineage payload for a trade, or None if its type isn't supported."""
    tt = trade.trade_type.value
    fn = _DISPATCH.get(tt)
    if fn is None:
        return None
    ql.Settings.instance().evaluationDate = cv.to_ql_date(ctx.valuation_date)
    with curve_cache():
        mds = engine.loader.resolve(trade.market_data_requirements(ctx), ctx)
        res = get_pricer(trade).price(trade, mds, ctx)
        body = fn(trade, ctx, mds, res)
        body["sensitivity_lineage"] = _sensitivity_lineage(trade, ctx, mds)
    header = {
        "trade_id": trade.trade_id, "trade_type": tt, "currency": trade.currency,
        "valuation_date": ctx.valuation_date.isoformat(),
        "warnings": list(getattr(mds, "warnings", []) or []),
    }
    return {**header, **body}
