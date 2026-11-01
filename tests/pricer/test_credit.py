"""CDS pricer tests (trades #28-30): mocked logic + real-data marks.

Validates the ISDA-calibrated flat-hazard pricing: par reprice, fair spread ==
market spread, buy/sell symmetry, CS01 signs, and golden PVs from real DTCC data.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.conventions import IndexConvention
from src.pricer.input.common.enums import CdsKind, MarketDataKind, ProtectionSide
from src.pricer.input.common.market_data import CdsSpreadData, MarketDataSet, ParOISCurveData
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.input.credit.instruments import CreditDefaultSwapTrade
from src.pricer.loader.credit_adapter import CreditMarketDataAdapter
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.ql.factory import get_pricer

VDATE = date(2026, 5, 29)

# Core default book @ 2026-05-29 (rebaselined 2026-07-27 realized risk-share cap: 38-trade book, scale-to-budget
# sizing, docs/design/portfolio_rebalance_review.md). Values carry the A2 CDS piecewise-hazard bootstrap.
# TRADE_28/30 rebaselined 2026-10-08 (credit-sdr-dedup: every DTCC record kept, only price-forming prints
# priced): 990,093.91 -> 969,618.66 (-2.1%) and 1,716,347.74 -> 1,705,232.72 (-0.65%); see golden_pvs.json.
# TRADE_28 again 969,618.66 -> 971,580.58 once the credit history reached back to keep_from_date (the
# 05-29 window had been truncated by the earlier run-date-relative retention).
# Re-sized 2026-10-11 (sizing re-run on 2026-10-06 data, run 6f2dc4d4): every pin scaled by its trade's
# position-size ratio (contract-count ratio for options); see golden_pvs.json rebaseline_history.
GOLDEN_PV = {
    "TRADE_28": 1_378_084.42,
    "TRADE_29": -498_857.88,
    "TRADE_30": 2_418_692.50,
}


@pytest.fixture
def ctx():
    return ValuationContext(valuation_date=VDATE)


@pytest.fixture
def curve_data():
    return ParOISCurveData("USD", IndexConvention.usd_sofr(), VDATE,
                           ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y"),
                           (0.040,) * 6, source="unit-test")


def _cds(reference="ACME", protection=ProtectionSide.BUY, coupon=0.01):
    return CreditDefaultSwapTrade(
        trade_id="C", description="cds", currency="USD", notional=10_000_000,
        reference=reference, protection=protection, kind=CdsKind.SINGLE_NAME,
        tenor="5Y", running_coupon=coupon, recovery=0.40,
    )


def _price(trade, ctx, curve_data, spread):
    cs = CdsSpreadData(trade.reference, "USD", spread, 0.40, VDATE, from_dtcc=True, source="test")
    mds = MarketDataSet(valuation_date=ctx.valuation_date)
    for req in trade.market_data_requirements(ctx).requirements:
        if req.kind == MarketDataKind.OIS_CURVE:
            mds.put(req, curve_data, curve_data.as_of_date)
        elif req.kind == MarketDataKind.CDS_SPREAD:
            mds.put(req, cs, cs.as_of_date)
    return get_pricer(trade).price(trade, mds, ctx)


# --------------------------------------------------------------------------- #
# Mocked logic
# --------------------------------------------------------------------------- #
def test_par_cds_reprices_to_zero(ctx, curve_data):
    # running coupon == market par spread -> NPV ~ 0
    r = _price(_cds(coupon=0.0150), ctx, curve_data, spread=0.0150)
    assert abs(r.pv) < 1.0
    assert r.measures["fair_spread_bp"] == pytest.approx(150.0, abs=0.5)


def test_fair_spread_equals_market(ctx, curve_data):
    r = _price(_cds(coupon=0.01), ctx, curve_data, spread=0.0120)
    assert r.measures["fair_spread_bp"] == pytest.approx(120.0, abs=0.5)


def test_buy_sell_opposite_sign(ctx, curve_data):
    buy = _price(_cds(protection=ProtectionSide.BUY), ctx, curve_data, spread=0.0120)
    sell = _price(_cds(protection=ProtectionSide.SELL), ctx, curve_data, spread=0.0120)
    assert buy.pv == pytest.approx(-sell.pv, rel=1e-6)


def test_buyer_value_positive_when_spread_above_coupon(ctx, curve_data):
    wide = _price(_cds(coupon=0.01), ctx, curve_data, spread=0.0200)   # 200 > 100
    tight = _price(_cds(coupon=0.01), ctx, curve_data, spread=0.0050)  # 50 < 100
    assert wide.pv > 0
    assert tight.pv < 0


def test_cs01_signs(ctx, curve_data):
    buy = _price(_cds(protection=ProtectionSide.BUY), ctx, curve_data, spread=0.0120)
    sell = _price(_cds(protection=ProtectionSide.SELL), ctx, curve_data, spread=0.0120)
    assert buy.measures["cs01"] > 0      # protection buyer gains as spreads widen
    assert sell.measures["cs01"] < 0


def test_survival_and_hazard_sane(ctx, curve_data):
    r = _price(_cds(), ctx, curve_data, spread=0.0120)
    assert 0.0 < r.measures["survival_to_maturity"] < 1.0
    # flat hazard ~ s/(1-R) = 200bp ballpark
    assert 150.0 < r.measures["hazard_bp"] < 260.0


# --------------------------------------------------------------------------- #
# A2 — piecewise hazard curve
# --------------------------------------------------------------------------- #
def _price_with_curve(trade, ctx, curve_data, curve_points):
    """Price with a term-structure CdsSpreadData (curve_points drives the bootstrap)."""
    spread5 = dict(curve_points)[5.0]
    cs = CdsSpreadData(trade.reference, "USD", spread5, 0.40, VDATE, from_dtcc=True,
                       source="test", curve_points=tuple(sorted(curve_points)))
    mds = MarketDataSet(valuation_date=ctx.valuation_date)
    for req in trade.market_data_requirements(ctx).requirements:
        if req.kind == MarketDataKind.OIS_CURVE:
            mds.put(req, curve_data, curve_data.as_of_date)
        elif req.kind == MarketDataKind.CDS_SPREAD:
            mds.put(req, cs, cs.as_of_date)
    return get_pricer(trade).price(trade, mds, ctx)


def test_piecewise_each_tenor_par_reprices():
    """The bootstrapped curve reprices a par CDS to ~0 at EVERY calibrated tenor —
    the defining property of a correct term-structure bootstrap."""
    import QuantLib as ql
    from src.pricer.ql.common import conventions as cv
    from src.pricer.ql.common.curve_builder import build_ois_curve
    from src.pricer.ql.credit.cds_pricer import _standard_schedule, calibrate_piecewise_hazard

    curve = ParOISCurveData("USD", IndexConvention.usd_sofr(), VDATE,
                            ("1Y", "3Y", "5Y", "7Y", "10Y"), (0.040,) * 5, source="unit-test")
    ql.Settings.instance().evaluationDate = cv.to_ql_date(VDATE)
    disc = build_ois_curve(curve, interpolation="flatforward")
    today = cv.to_ql_date(VDATE)
    points = [(1.0, 0.0060), (3.0, 0.0110), (5.0, 0.0175)]     # upward-sloping IG curve
    prob, nodes = calibrate_piecewise_hazard(points, 0.40, disc, today)
    assert [n["tenor"] for n in nodes] == ["1Y", "3Y", "5Y"]
    for (ty, sp), n in zip(points, nodes, strict=True):
        sched = _standard_schedule(today, f"{int(ty)}Y")
        par = ql.CreditDefaultSwap(ql.Protection.Buyer, 1.0, sp, sched, ql.Following, ql.Actual360())
        par.setPricingEngine(ql.IsdaCdsEngine(prob, 0.40, disc))
        assert abs(par.NPV()) < 1e-4, f"{n['tenor']} par CDS did not reprice to 0: {par.NPV()}"
    # survival strictly decreasing across the curve
    surv = [n["survival"] for n in nodes]
    assert surv[0] > surv[1] > surv[2] > 0.0


def test_piecewise_matches_flat_when_curve_is_flat(ctx, curve_data):
    """A flat *spread* term structure must land close to the single-flat-hazard PV.
    (Not exactly equal: a flat spread curve implies a mildly non-flat hazard, so the
    1Y/5Y bootstrap differs from a 5Y-only flat-hazard calibration by a small amount —
    here ~0.2%. The point is the piecewise path introduces no large bias.)"""
    trade = _cds(coupon=0.01)
    flat = _price(trade, ctx, curve_data, spread=0.0150)
    curved = _price_with_curve(trade, ctx, curve_data, [(1.0, 0.0150), (5.0, 0.0150)])
    assert curved.diagnostics["hazard_curve"] == "piecewise"
    assert curved.pv == pytest.approx(flat.pv, rel=3e-3)


def _index_cds(factor=1.0, coupon=0.01, protection=ProtectionSide.SELL):
    return CreditDefaultSwapTrade(
        trade_id="IDX", description="cdx", currency="USD", notional=10_000_000,
        reference="CDX.NA.IG", protection=protection, kind=CdsKind.INDEX,
        tenor="5Y", running_coupon=coupon, recovery=0.40)


def _price_index(trade, ctx, curve_data, spread, factor):
    cs = CdsSpreadData(trade.reference, "USD", spread, 0.40, VDATE, from_dtcc=True,
                       source="test", index_factor=factor, n_constituents=125)
    mds = MarketDataSet(valuation_date=ctx.valuation_date)
    for req in trade.market_data_requirements(ctx).requirements:
        if req.kind == MarketDataKind.OIS_CURVE:
            mds.put(req, curve_data, curve_data.as_of_date)
        elif req.kind == MarketDataKind.CDS_SPREAD:
            mds.put(req, cs, cs.as_of_date)
    return get_pricer(trade).price(trade, mds, ctx)


def test_rec01_reported_and_bounded(ctx, curve_data):
    """B5: rec01 (recovery risk, spread held constant) is reported and is small relative to
    CS01 — by construction, holding the spread fixed makes the mark insensitive to recovery
    (the large recovery effect is the default loss, captured by the JTD add-on)."""
    r = _price(_cds(protection=ProtectionSide.BUY), ctx, curve_data, spread=0.0175)
    assert "rec01" in r.measures
    assert abs(r.measures["rec01"]) < abs(r.measures["cs01"])   # second-order vs spread risk


def test_rec01_flips_sign_with_protection(ctx, curve_data):
    buy = _price(_cds(protection=ProtectionSide.BUY), ctx, curve_data, spread=0.0175)
    sell = _price(_cds(protection=ProtectionSide.SELL), ctx, curve_data, spread=0.0175)
    assert buy.measures["rec01"] == pytest.approx(-sell.measures["rec01"], rel=1e-6)


def test_index_factor_scales_notional(ctx, curve_data):
    """B4: the index factor scales the outstanding notional, so PV scales linearly with it."""
    full = _price_index(_index_cds(), ctx, curve_data, spread=0.0060, factor=1.0)
    half = _price_index(_index_cds(), ctx, curve_data, spread=0.0060, factor=0.5)
    assert half.pv == pytest.approx(0.5 * full.pv, rel=1e-9)
    assert full.measures["n_constituents"] == 125
    assert "index-level" in full.diagnostics["engine"]
    assert "untranched" in full.diagnostics["index_model"]


@pytest.mark.needs_market_data
def test_index_diagnostics_on_real_cdx(priced):
    _, r = priced["TRADE_30"]                            # CDX.NA.IG sell protection
    assert r.measures["index_factor"] == 1.0
    assert r.measures["n_constituents"] == 125
    assert "index-level" in r.diagnostics["engine"]


@pytest.mark.needs_market_data
def test_orcl_resolves_term_structure_meta_cdx_flat():
    """Real data: ORCL builds a >=2-tenor curve (piecewise); META (thin) and CDX (index)
    stay flat."""
    adapter = CreditMarketDataAdapter(load_pricer_config(), get_project_root())
    orcl, _, _ = adapter.cds_spread("ORCL", ValuationContext(valuation_date=VDATE))
    assert len(orcl.curve_points) >= 2 and (5.0, orcl.spread) in orcl.curve_points
    for ref in ("META", "CDX.NA.IG"):
        cs, _, _ = adapter.cds_spread(ref, ValuationContext(valuation_date=VDATE))
        assert cs.curve_points == ()


# --------------------------------------------------------------------------- #
# Real data
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def priced():
    root = get_project_root()
    config = load_pricer_config()
    ctx = ValuationContext(valuation_date=VDATE)
    loader = MarketDataLoader(config, root)
    out = {}
    for trade in load_portfolio(asset_class="credit"):
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        out[trade.trade_id] = (trade, get_pricer(trade).price(trade, mds, ctx))
    return out


@pytest.mark.needs_market_data
@pytest.mark.parametrize("ref,lo,hi", [("ORCL", 80, 350), ("META", 30, 200), ("CDX.NA.IG", 20, 120)])
def test_dtcc_spreads_realistic(ref, lo, hi):
    adapter = CreditMarketDataAdapter(load_pricer_config(), get_project_root())
    cs, _as_of, _w = adapter.cds_spread(ref, ValuationContext(valuation_date=VDATE))
    assert cs.from_dtcc is True, f"{ref} did not resolve from DTCC"
    assert lo < cs.spread * 1e4 < hi, f"{ref} spread {cs.spread*1e4:.1f}bp out of range"


@pytest.mark.needs_market_data
def test_protection_signs(priced):
    assert priced["TRADE_28"][1].pv > 0    # ORCL buy, par ~175 > 100bp coupon
    assert priced["TRADE_29"][1].pv < 0    # META buy, par ~74 < 100bp coupon
    assert priced["TRADE_30"][1].measures["par_spread_bp"] < 100  # CDX IG below coupon


@pytest.mark.needs_market_data
def test_fair_spread_matches_par_spread(priced):
    for _t, r in priced.values():
        assert r.measures["fair_spread_bp"] == pytest.approx(r.measures["par_spread_bp"], abs=0.5)


@pytest.mark.needs_market_data
@pytest.mark.parametrize("tid", list(GOLDEN_PV))
def test_golden_pv_real_data(priced, tid):
    _, r = priced[tid]
    assert r.pv == pytest.approx(GOLDEN_PV[tid], rel=2e-3), f"{tid}: {r.pv:,.2f}"


def test_credit_history_covers_pinned_valuation_dates():
    # The credit ingest keeps records from keep_from_date (configs/marketdata/credit); the pricer looks
    # back curve_lookback_days from the valuation date. Every pinned valuation date (this module's VDATE,
    # the bond real-data tests' date, the golden baseline) must have its full window on disk, otherwise
    # its spreads quietly thin out as old records age out. Config-only: no market data needed.
    root = get_project_root()
    cfg = load_pricer_config()
    sources = json.loads((root / "configs/marketdata/credit/credit_marketdata.json").read_text(encoding="utf-8"))
    golden = json.loads((root / "configs/pricer/golden_pvs.json").read_text(encoding="utf-8"))
    lookback = max(int(cfg["credit"]["curve_lookback_days"]),
                   int(cfg["bonds"]["credit_spread"].get("curve_lookback_days", 365)))
    pinned = [VDATE, date.fromisoformat(golden["valuation_date"])]
    needed = min(pinned) - timedelta(days=lookback)
    for name in ("dtcc_single_name", "dtcc_index"):
        keep_from = date.fromisoformat(sources["sources"][name]["keep_from_date"])
        assert keep_from <= needed, f"{name}: keep_from_date {keep_from} > {needed} needed by {min(pinned)}"
