"""Real-market-data integration & validation tests for the bond book (#15-21).

Drives the real CSVs in data/marketdata/ through the actual loader and asserts:
  1. Cross-source consistency — UST YTM matches the Treasury par yield at its
     maturity; corporate YTMs sit above the comparable UST (positive credit spread).
  2. Curve accuracy — a par UST reprices to ~100 off the bootstrapped curve.
  3. Credit-spread fallback behaves as documented (free CDS unusable -> config + warn).
  4. Recovered Z-spread matches the input credit spread.
  5. Golden PV regression for all 7 bonds.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
import QuantLib as ql
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.bonds_adapter import BondsMarketDataAdapter
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.portfolio.portfolio_loader import load_portfolio
from src.pricer.ql.common import conventions as cv
from src.pricer.ql.common.curve_builder import build_treasury_curve
from src.pricer.ql.factory import get_pricer

VALUATION_DATE = date(2026, 5, 29)

# Core default book @ 2026-05-29 (rebaselined 2026-07-27 realized risk-share cap: 38-trade book, scale-to-budget
# sizing, docs/design/portfolio_rebalance_review.md). 8 bonds: corp #15-19, USTs #20-21, plus the
# Part-2 1Y-UST liquidity buffer (#36). Values carry the current curve/credit methodology (A1/A2/A3).
# TRADE_17 rebaselined 2026-10-08 (credit-sdr-dedup: every DTCC record kept, only price-forming prints
# priced): 71,443,330.65 -> 71,569,981.55 (+0.18%); see golden_pvs.json rebaseline_history.
# Re-sized 2026-10-11 (sizing re-run on 2026-10-06 data, run 6f2dc4d4): every pin scaled by its trade's
# position-size ratio (contract-count ratio for options); see golden_pvs.json rebaseline_history.
GOLDEN_PV = {
    "TRADE_15": 114_522_231.99,
    "TRADE_16": 57_019_093.48,
    "TRADE_17": 101_514_459.49,
    "TRADE_18": 113_085_117.00,
    "TRADE_19": 115_879_819.83,
    "TRADE_20": 112_575_133.74,
    "TRADE_21": 113_239_668.62,
    "TRADE_36": 113_451_836.39,
}


@pytest.fixture(scope="module")
def root():
    return get_project_root()


@pytest.fixture(scope="module")
def config():
    return load_pricer_config()


@pytest.fixture(scope="module")
def ctx():
    return ValuationContext(valuation_date=VALUATION_DATE)


@pytest.fixture(scope="module")
def loader(config, root):
    return MarketDataLoader(config, root)


@pytest.fixture(scope="module")
def priced(loader, ctx):
    out = {}
    for trade in load_portfolio(asset_class="bonds"):
        mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
        out[trade.trade_id] = (trade, get_pricer(trade).price(trade, mds, ctx))
    return out


def _tsy_par(root, col):
    df = pd.read_csv(root / "data" / "marketdata" / "bonds" / "treasury_par_yields.csv",
                     parse_dates=["date"])
    df = df[df["date"] <= pd.Timestamp(VALUATION_DATE)].sort_values("date")
    return float(df.iloc[-1][col]) / 100.0


# --------------------------------------------------------------------------- #
# 1. Cross-source consistency
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_ust_10y_ytm_matches_par_yield(root, priced):
    _, r = priced["TRADE_20"]
    par10 = _tsy_par(root, "t10yr")
    assert abs(r.measures["ytm"] - par10) < 10e-4, f"UST10Y YTM {r.measures['ytm']:.4%} vs par {par10:.4%}"
    # coupon 4.375% < ~4.45% par yield -> priced below par
    assert r.measures["clean_price"] < 100.0


@pytest.mark.needs_market_data
def test_ust_2y_near_par(priced):
    _, r = priced["TRADE_21"]
    # coupon 4.0% ~ 2Y par yield -> within a point of par
    assert abs(r.measures["clean_price"] - 100.0) < 1.5


def _finra_otr_vwap(root, bucket: str) -> float | None:
    """Latest FINRA TRACE on-the-run volume-weighted average price for a maturity bucket
    on/before the valuation date (an independent traded-price source for the USTs)."""
    df = pd.read_csv(root / "data" / "marketdata" / "bonds" / "finra_treasury_daily.csv",
                     parse_dates=["date"])
    df["vwap"] = pd.to_numeric(df["volumeWeightedAveragePrice"], errors="coerce")
    sub = df[(df["benchmark"] == "On-the-run") & (df["yearsToMaturity"] == bucket)
             & (df["date"] <= pd.Timestamp(VALUATION_DATE))].dropna(subset=["vwap"]).sort_values("date")
    return None if sub.empty else float(sub.iloc[-1]["vwap"])


@pytest.mark.needs_market_data
@pytest.mark.parametrize("tid,bucket,tol", [
    ("TRADE_20", "> 7 years and <= 10 years", 2.5),   # 10Y OTR
    ("TRADE_21", "<= 2 years", 1.5),                  # 2Y OTR
])
def test_ust_clean_price_vs_finra_trace(root, priced, tid, bucket, tol):
    """C5: cross-check the model clean price of the on-the-run USTs against FINRA TRACE traded
    prices (an independent source). The FINRA figure is the bucket's VWAP, so the tolerance is
    loose — this is a sanity gate, not an exact reconciliation."""
    finra = _finra_otr_vwap(root, bucket)
    if finra is None:
        pytest.skip(f"no FINRA on-the-run VWAP for {bucket}")
    model = priced[tid][1].measures["clean_price"]
    assert abs(model - finra) < tol, f"{tid} model clean {model:.3f} vs FINRA TRACE {finra:.3f}"


@pytest.mark.needs_market_data
def test_corp_yield_above_treasury(root, priced):
    """Each corporate must yield more than the comparable-maturity UST
    (positive credit spread) — a basic real-world consistency check."""
    ust10 = priced["TRADE_20"][1].measures["ytm"]
    # AAPL 2035 (~9Y) and MSFT 2037 (~11Y) bracket the 10Y UST
    assert priced["TRADE_15"][1].measures["ytm"] > ust10
    assert priced["TRADE_16"][1].measures["ytm"] > ust10


@pytest.mark.needs_market_data
def test_corp_zspread_matches_configured_spread(priced):
    """Recovered Z-spread ~ the input credit spread (AAPL fallback 60bp)."""
    _, r = priced["TRADE_15"]
    assert r.measures["credit_spread_bp"] == pytest.approx(60.0, abs=0.1)
    assert abs(r.measures["z_spread_bp"] - 60.0) < 4.0


# --------------------------------------------------------------------------- #
# 2. Curve accuracy
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_treasury_par_bond_reprices_to_par(config, root):
    adapter = BondsMarketDataAdapter(config, root)
    curve_data, _as_of, _w = adapter.treasury_curve(ValuationContext(valuation_date=VALUATION_DATE))
    ql.Settings.instance().evaluationDate = cv.to_ql_date(VALUATION_DATE)
    handle = build_treasury_curve(curve_data, settlement_days=1)
    cal = ql.UnitedStates(ql.UnitedStates.GovernmentBond)
    dc = ql.ActualActual(ql.ActualActual.Bond)
    settle = cal.advance(cv.to_ql_date(VALUATION_DATE), 1, ql.Days)
    par10 = dict(zip(curve_data.tenors, curve_data.par_yields, strict=True))["10Y"]
    sched = ql.MakeSchedule(settle, cal.advance(settle, ql.Period("10Y")),
                            ql.Period(ql.Semiannual), calendar=cal,
                            convention=ql.Unadjusted, backwards=True)
    bond = ql.FixedRateBond(1, 100.0, sched, [par10], dc)
    bond.setPricingEngine(ql.DiscountingBondEngine(handle))
    assert bond.cleanPrice() == pytest.approx(100.0, abs=0.1)


# --------------------------------------------------------------------------- #
# 3. Credit-spread fallback behaviour
# --------------------------------------------------------------------------- #
def test_credit_spread_falls_back_to_config(config, root, ctx):
    """An issuer with no clean single-name CDS quotes (AAPL) uses the config fallback."""
    adapter = BondsMarketDataAdapter(config, root)
    cs, _as_of, warns = adapter.credit_spread("AAPL", ctx)
    assert cs.from_cds is False              # AAPL has no clean free single-name CDS
    assert cs.source == "config-fallback"
    assert cs.spread == pytest.approx(0.0060)   # AAPL config 60bp (Z-spread over SOFR)
    assert any("fallback" in w.lower() for w in warns)


@pytest.mark.needs_market_data
def test_credit_spread_from_single_name_cds(config, root, ctx):
    """ORCL/META derive a real 5Y par CDS spread from the credit-desk single-name
    feed (the same file the CDS trades #28-30 use), lifted to a Z-spread over SOFR
    by the SOFR-Treasury basis DERIVED from the free Treasury + SOFR curves — no
    config fallback and no hardcoded basis constant."""
    adapter = BondsMarketDataAdapter(config, root)
    basis_bp = adapter._sofr_treasury_basis_bp(ctx)     # Treasury - SOFR at basis_tenor
    assert 20.0 < basis_bp < 60.0                       # ~42bp at 10Y on 2026-05-29
    # No exact spread pins (the medians move with each data refresh; exact levels are the golden PVs'
    # job). Instead the expected 5Y CDS median is recomputed here, independently of the adapter, with
    # plain pandas over the stored file: new trades only (NEWT + TRAD), executed in the 120d window,
    # disseminated by the valuation date, issuer names minus exclusions, 5-500bp band, expiry within
    # 120d of valuation + 5Y.
    cs_cfg = config["bonds"]["credit_spread"]
    raw = pd.read_csv(root / "data/marketdata/credit/dtcc_single_name_cds.csv", dtype=str,
                      keep_default_na=False, na_values=[""])
    vts = pd.Timestamp(VALUATION_DATE)
    executed = pd.to_datetime(raw["Execution Timestamp"], errors="coerce", utc=True,
                              format="ISO8601").dt.tz_localize(None).dt.normalize()
    sp = pd.to_numeric(raw["Spread-Leg 1"], errors="coerce")
    expiry = pd.to_datetime(raw["Expiration Date"], errors="coerce")
    new_trades = ((raw["Action type"] == "NEWT") & (raw["Event type"] == "TRAD")
                  & (raw["date"] <= VALUATION_DATE.isoformat())
                  & (executed > vts - pd.Timedelta(days=120)) & (executed <= vts)
                  & sp.between(5e-4, 5e-2)
                  & ((expiry - (vts + pd.DateOffset(years=5))).abs().dt.days <= 120))
    name = raw["Underlying Asset Name"].fillna("").str.upper()
    excluded = name.str.contains("BIDCO|HOLDCO|AVIATION|AMENTUM|ATLANTIC")
    for issuer in ("ORCL", "META"):
        names = [n.upper() for n in cs_cfg["issuer_cds_names"][issuer]]
        bucket = sp[new_trades & name.apply(lambda s, ns=names: any(n in s for n in ns)) & ~excluded]
        assert len(bucket) >= 3, f"{issuer}: too few 5Y new trades on disk to price from DTCC"
        expected_cds_bp = float(bucket.median()) * 1e4
        assert 20.0 < expected_cds_bp < 600.0                    # realistic IG 5Y single-name CDS

        cs, _as_of, warns = adapter.credit_spread(issuer, ctx)
        assert cs.from_cds is True, f"{issuer} should source from DTCC single-name"
        assert cs.source == "dtcc_single_name"
        assert warns == []                      # clean source, full history -> no warnings
        # independent recomputation and the adapter agree exactly (same data, same rule)
        assert cs.spread == pytest.approx((expected_cds_bp + basis_bp) / 1e4, abs=1e-10)
        # still a realistic IG level that keeps the corporate above the comparable UST
        assert 0.0 < cs.spread < 0.05


@pytest.mark.needs_market_data
def test_long_end_bonds_within_extended_curve(priced):
    # A1: the SOFR grid now reaches 30Y, so ORCL 2053 (~27Y) and META 2054 (~28Y) sit
    # WITHIN the bootstrapped curve and no longer trigger a flat-extrapolation warning —
    # their long cashflows discount on real 12Y/15Y/20Y nodes (+ a constant-forward 30Y)
    # instead of a flat 10Y last node. This is the correctness gain A1 delivers.
    for tid in ("TRADE_17", "TRADE_18"):
        _, r = priced[tid]
        assert not any("extrapolation" in w.lower() for w in r.warnings), \
            f"{tid} still flags extrapolation: {r.warnings}"


@pytest.mark.needs_market_data
def test_corp_bond_credit_curve(priced):
    # A3: ORCL's CDS feed gives >=2 tenors, so its bond discounts on a Z-spread term
    # structure; META (single 5Y) and the config-fallback issuers stay flat.
    _, orcl = priced["TRADE_17"]
    assert orcl.diagnostics["credit_spread_curve"] == "term_structure"
    assert orcl.measures["credit_curve_tenors"] >= 2
    _, meta = priced["TRADE_18"]
    assert meta.diagnostics["credit_spread_curve"] == "flat"


def test_credit_spread_curve_upward_and_front_richer_than_flat():
    # A3 mechanics: an upward Z-spread curve discounts near-term cashflows at a lower
    # spread than a flat 5Y spread, so the term-structure PV exceeds the flat PV.
    import QuantLib as ql
    from src.pricer.input.common.conventions import IndexConvention
    from src.pricer.input.common.market_data import ParOISCurveData
    from src.pricer.ql.common import conventions as cv
    from src.pricer.ql.common.curve_builder import (
        build_ois_curve,
        build_zero_spreaded_curve,
        build_zero_spreaded_curve_ts,
    )

    ql.Settings.instance().evaluationDate = cv.to_ql_date(VALUATION_DATE)
    sofr_data = ParOISCurveData("USD", IndexConvention.usd_sofr(), VALUATION_DATE,
                                ("1Y", "3Y", "5Y", "10Y"), (0.040,) * 4, source="unit-test")
    sofr = build_ois_curve(sofr_data)
    ref = sofr.currentLink().referenceDate()
    pts = ((1.0, 0.0044), (5.0, 0.0219))          # steep IG-style curve
    ts = build_zero_spreaded_curve_ts(sofr, pts, ref)
    flat = build_zero_spreaded_curve(sofr, 0.0219)
    d3_ts = ts.discount(ref + ql.Period(3, ql.Years))
    d3_flat = flat.discount(ref + ql.Period(3, ql.Years))
    assert d3_ts > d3_flat                          # lower front spread -> higher near DF
    # long end (>5Y) flat-extrapolates the last spread -> matches the flat 219bp curve
    d8_ts = ts.discount(ref + ql.Period(8, ql.Years))
    d8_flat = flat.discount(ref + ql.Period(8, ql.Years))
    assert abs(d8_ts - d8_flat) / d8_flat < 5e-3


# --------------------------------------------------------------------------- #
# 4. Sanity + golden PVs
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_all_bonds_priced_positive(priced):
    for tid, (_t, r) in priced.items():
        assert r.pv > 0, f"{tid} non-positive PV {r.pv}"
        assert r.measures["mod_duration"] > 0
        assert r.measures["dv01"] > 0


@pytest.mark.needs_market_data
@pytest.mark.parametrize("tid", list(GOLDEN_PV))
def test_golden_pv_real_data(priced, tid):
    _, r = priced[tid]
    assert r.pv == pytest.approx(GOLDEN_PV[tid], rel=1e-3), f"{tid}: {r.pv:,.2f}"
