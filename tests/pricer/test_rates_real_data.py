"""Real-market-data integration & validation tests for the rates book.

Unlike test_pricers_mocked.py (synthetic curve, isolated logic), this module drives
the *real* CSVs in data/marketdata/ through the actual loader, and asserts:

  1. Cross-source consistency — independent free data sources agree (the SOFR curve
     front matches the realized SOFR fixing; the 10Y SOFR rate sits a sensible swap
     spread from the 10Y UST). "All data points consistent with each other."
  2. Curve accuracy — the bootstrapped curve reproduces the Pensford forward grid and
     reprices every par node back to par.
  3. Model-independent financial identities — swaption put/call parity, ATM symmetry,
     Bermudan >= European, vol monotonicity.
  4. Risk — analytic DV01 vs a finite-difference curve bump.
  5. Golden PV regression — locks the real-data PVs so code changes are caught.

Valuation date 2026-05-29 is the latest Pensford snapshot; on that date FRED, NY Fed,
Pensford and MOVE all have data, so the snapshot is internally consistent.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
import QuantLib as ql
from conftest import mock_resolve
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.enums import SwapDirection
from src.pricer.input.common.market_data import ParOISCurveData
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.input.rates.instruments import (
    BermudanSwaptionTrade,
    CapTrade,
    EuropeanSwaptionTrade,
    OvernightIndexedSwapTrade,
)
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.loader.rates_adapter import RatesMarketDataAdapter
from src.pricer.ql.common import conventions as cv
from src.pricer.ql.common.curve_builder import build_ois_curve
from src.pricer.ql.rates.pricer_factory import get_pricer

VALUATION_DATE = date(2026, 5, 29)
NOTIONAL = 50_000_000

# Golden PVs computed from the committed CSVs + pricer_config.json (MOVE-derived flat
# normal vol). Regenerate intentionally if the data snapshot or model changes.
# Fixed 50mm notional (book-independent) @ 2026-05-29; rebaselined 2026-07-11 to the
# current Pensford SOFR curve + MOVE-derived normal vol (~70.2bp on 2026-05-29).
# Rebaselined 2026-07-12 (A1): SOFR par grid extended past 10Y (12Y/15Y/20Y real nodes
# + constant-forward-extrapolated 30Y). The 5Yx10Y swaption (TRADE_23) reprices -0.28%
# as its ~15Y underlying now discounts on a proper steepening long end instead of a flat
# 10Y node; the 10Y swap/cap are effectively unchanged.
# Rebaselined 2026-07-12 (C1): the flat MOVE normal vol is shaped into an expiry-dependent
# term structure (stylized MOVE-anchored multiplier), so each option gets its expiry's vol and
# the Bermudan calibration sees a real slope. 5Yx10Y swaption -3.0%, Bermudan -2.4%, 1Y cap -0.9%.
GOLDEN_PV = {
    "TRADE_22": 956_628.01,
    "TRADE_23": 2_025_608.14,
    "TRADE_24": 986_137.56,
    "TRADE_25": 79_475.04,
}


# --------------------------------------------------------------------------- #
# Fixtures — real data
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def root():
    return get_project_root()


@pytest.fixture(scope="module")
def config():
    return load_pricer_config()


@pytest.fixture(scope="module")
def real_ctx():
    return ValuationContext(valuation_date=VALUATION_DATE)


@pytest.fixture(scope="module")
def adapter(config, root):
    return RatesMarketDataAdapter(config, root)


@pytest.fixture(scope="module")
def real_curve(adapter, real_ctx) -> ParOISCurveData:
    curve, _as_of, _warns = adapter.par_ois_curve(real_ctx)
    return curve


@pytest.fixture(scope="module")
def real_vol(adapter, real_ctx):
    vol, _ = adapter.normal_vol(None, real_ctx)
    return vol


@pytest.fixture(scope="module")
def loader(config, root):
    return MarketDataLoader(config, root)


def _price(trade, loader, ctx):
    mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
    return get_pricer(trade).price(trade, mds, ctx)


def _csv_value_on(root, rel, datecol, valcol, on=VALUATION_DATE):
    """Latest value of `valcol` on/before `on` from a market-data CSV (decimal %)."""
    df = pd.read_csv(root / "data" / "marketdata" / rel)
    df[datecol] = pd.to_datetime(df[datecol], errors="coerce")
    df = df.dropna(subset=[datecol, valcol]).sort_values(datecol)
    return float(df[df[datecol] <= pd.Timestamp(on)].iloc[-1][valcol])


# --------------------------------------------------------------------------- #
# 1. Cross-source consistency
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_snapshot_fresh_and_no_warnings(adapter, real_ctx):
    _curve, as_of, warns = adapter.par_ois_curve(real_ctx)
    assert as_of == VALUATION_DATE
    assert warns == []


def test_normal_vol_term_structure(adapter, real_ctx):
    """C1: the MOVE level is shaped into an expiry-dependent normal-vol term structure; each
    option reads its expiry's vol via vol_at (linear-interp, flat-extrapolated)."""
    vol, _ = adapter.normal_vol("5Y", real_ctx)
    assert vol.term_structure                                  # non-empty
    assert vol.vol_at(1) >= vol.vol_at(5) >= vol.vol_at(10)    # gentle stylized decline
    assert vol.vol_at(1 / 12) == pytest.approx(vol.vol, rel=1e-6)   # 1M anchor == headline level
    assert vol.vol_at(30) == vol.vol_at(10)                    # flat-extrapolated past the last node
    # a swaption at a longer expiry gets a slightly lower vol than one at the short end
    assert vol.vol_at(2) > vol.vol_at(6)


@pytest.mark.needs_market_data
def test_normal_vol_is_move_derived(adapter, real_ctx):
    """Swaption/cap normal vol is the point-in-time MOVE level (annualized bp of
    yield vol ≈ a Bachelier normal vol), not a frozen constant."""
    vol, as_of = adapter.normal_vol("5Y", real_ctx)
    assert vol.vol == pytest.approx(70.22 / 1e4, abs=1e-4)   # MOVE 70.22 on 2026-05-29
    assert "MOVE-derived" in vol.anchor
    assert as_of == VALUATION_DATE
    # far below the old frozen 90bp -> a live, market-driven level
    assert 0.0010 < vol.vol < 0.0090


@pytest.mark.needs_market_data
def test_all_sources_cover_valuation_date(root):
    """FRED, NY Fed and Pensford must all have data on/around the valuation date,
    otherwise the snapshot is not a coherent single-day market."""
    sofr_fred = _csv_value_on(root, "rates/fred_rates.csv", "date", "SOFR")
    sofr_nyfed = _csv_value_on(root, "rates/nyfed_sofr_rates.csv", "date", "sofr_rate")
    dgs10 = _csv_value_on(root, "rates/fred_rates.csv", "date", "DGS10")
    assert 0 < sofr_fred < 10 and 0 < sofr_nyfed < 10 and 0 < dgs10 < 10


@pytest.mark.needs_market_data
def test_curve_front_matches_realized_sofr(root, real_curve):
    """The bootstrapped SOFR curve's overnight front must agree with the realized
    SOFR fixing from NY Fed / FRED (two independent sources) to within ~15bp."""
    sofr_fred = _csv_value_on(root, "rates/fred_rates.csv", "date", "SOFR") / 100.0
    sofr_nyfed = _csv_value_on(root, "rates/nyfed_sofr_rates.csv", "date", "sofr_rate") / 100.0
    # the two raw sources must themselves agree
    assert abs(sofr_fred - sofr_nyfed) < 5e-4

    ql.Settings.instance().evaluationDate = cv.to_ql_date(VALUATION_DATE)
    ts = build_ois_curve(real_curve).currentLink()
    today = cv.to_ql_date(VALUATION_DATE)
    d1m = cv.to_calendar(real_curve.index.calendar).advance(today, ql.Period("1M"))
    front = ts.forwardRate(today, d1m, ql.Actual360(), ql.Simple).rate()
    assert abs(front - sofr_fred) < 15e-4, f"curve front {front:.4%} vs SOFR {sofr_fred:.4%}"


@pytest.mark.needs_market_data
def test_10y_sofr_vs_ust_swap_spread(root, real_curve):
    """10Y SOFR par rate vs 10Y UST yield must imply a realistic swap spread.
    Post-2020 USD 10Y swap spreads are modestly negative; band [-90, +25] bp."""
    par10 = dict(zip(real_curve.tenors, real_curve.par_rates, strict=True))["10Y"]
    dgs10 = _csv_value_on(root, "rates/fred_rates.csv", "date", "DGS10") / 100.0
    swap_spread_bp = (par10 - dgs10) * 1e4
    assert -90.0 < swap_spread_bp < 25.0, f"implausible 10Y swap spread {swap_spread_bp:.1f}bp"


# --------------------------------------------------------------------------- #
# 2. Curve accuracy
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_par_nodes_reprice_to_par(real_curve):
    ql.Settings.instance().evaluationDate = cv.to_ql_date(VALUATION_DATE)
    handle = build_ois_curve(real_curve)
    index = cv.to_overnight_index(real_curve.index, handle)
    for tenor, par in zip(real_curve.tenors, real_curve.par_rates, strict=True):
        swap = ql.MakeOIS(cv.to_ql_period(tenor), index, par, ql.Period("0D"),
                          nominal=10_000_000, discountingTermStructure=handle, paymentLag=2)
        assert abs(swap.fairRate() - par) < 1e-5, f"{tenor}: {swap.fairRate()} vs {par}"


@pytest.mark.needs_market_data
def test_curve_extends_past_10y(real_curve):
    """A1: the SOFR par grid now carries long-end nodes past 10Y (12Y/15Y/20Y are real
    Pensford data; 30Y is constant-forward extrapolated). The long end must be present
    and monotonically increasing — the ORCL 2053 / META 2054 bonds and the 5Yx10Y
    swaption underlying rely on it instead of a flat 10Y extrapolation."""
    d = dict(zip(real_curve.tenors, real_curve.par_rates, strict=True))
    for t in ("12Y", "15Y", "20Y", "30Y"):
        assert t in d, f"missing long-end node {t}"
    # upward-sloping long end (this SOFR snapshot); each node strictly above the 10Y
    assert d["10Y"] < d["12Y"] < d["15Y"] < d["20Y"] < d["30Y"]
    # sane band — not the degenerate flat-off-10Y (~4.03%) nor a runaway extrapolation
    assert 0.040 < d["30Y"] < 0.050


@pytest.mark.needs_market_data
def test_long_end_forward_is_positive_not_flat(real_curve):
    """The constant-forward extrapolation past the ~20Y data horizon must keep a
    non-degenerate positive forward (np.interp's flat clamp would freeze the DF and
    imply a ~0% forward beyond 20Y, badly over-discounting the long bonds)."""
    ql.Settings.instance().evaluationDate = cv.to_ql_date(VALUATION_DATE)
    ts = build_ois_curve(real_curve).currentLink()
    d25 = cv.to_ql_date(VALUATION_DATE) + ql.Period("25Y")
    d30 = cv.to_ql_date(VALUATION_DATE) + ql.Period("30Y")
    fwd_25_30 = ts.forwardRate(d25, d30, ql.Actual360(), ql.Simple).rate()
    assert 0.02 < fwd_25_30 < 0.07, f"degenerate long-end forward {fwd_25_30:.4%}"


@pytest.mark.needs_market_data
def test_curve_reproduces_pensford_front_year(root, real_curve):
    """Within the first year (where the 1Y cap's caplets live) the bootstrapped
    curve must reproduce Pensford's monthly forwards tightly."""
    df = pd.read_csv(root / "data" / "marketdata" / "rates" / "pensford_sofr_forward.csv",
                     parse_dates=["as_of_date", "settlement_date"])
    sub = (df[df.as_of_date == pd.Timestamp(VALUATION_DATE)]
           .sort_values("settlement_date").reset_index(drop=True).head(13))

    ql.Settings.instance().evaluationDate = cv.to_ql_date(VALUATION_DATE)
    ts = build_ois_curve(real_curve).currentLink()
    dc = ql.Actual360()
    errs = []
    for i in range(len(sub) - 1):
        s1 = cv.to_ql_date(sub.settlement_date[i].date())
        s2 = cv.to_ql_date(sub.settlement_date[i + 1].date())
        cf = ts.forwardRate(s1, s2, dc, ql.Simple).rate()
        errs.append(abs(cf - sub.forward_sofr[i]) * 1e4)
    assert max(errs) < 10.0, f"front-year forward reproduction max err {max(errs):.1f}bp"


# --------------------------------------------------------------------------- #
# 3. Model-independent financial identities (real curve)
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_swaption_put_call_parity(loader, real_ctx):
    """payer_swaption(K) - receiver_swaption(K) == forward payer-swap PV struck at K.
    Holds for any vol — validates swaption vs swap pricer + curve consistency."""
    K = 0.045
    payer = EuropeanSwaptionTrade("PCP_P", "5x10 payer", "USD", NOTIONAL,
                                  direction=SwapDirection.PAYER, strike=K)
    recv = EuropeanSwaptionTrade("PCP_R", "5x10 receiver", "USD", NOTIONAL,
                                 direction=SwapDirection.RECEIVER, strike=K)
    fwd_swap = OvernightIndexedSwapTrade("PCP_S", "fwd payer swap", "USD", NOTIONAL,
                                         direction=SwapDirection.PAYER, swap_tenor="10Y",
                                         fixed_rate=K, forward_start="5Y")
    lhs = _price(payer, loader, real_ctx).pv - _price(recv, loader, real_ctx).pv
    rhs = _price(fwd_swap, loader, real_ctx).pv
    assert abs(lhs - rhs) < 1.0, f"put-call parity broken: {lhs:,.2f} vs {rhs:,.2f}"


@pytest.mark.needs_market_data
def test_atm_swaption_symmetry(loader, real_ctx):
    """At the ATM-forward strike, payer and receiver swaptions are worth the same."""
    payer = EuropeanSwaptionTrade("SYM_P", "5x10 payer ATM", "USD", NOTIONAL,
                                  direction=SwapDirection.PAYER)
    recv = EuropeanSwaptionTrade("SYM_R", "5x10 receiver ATM", "USD", NOTIONAL,
                                 direction=SwapDirection.RECEIVER)
    pv_p = _price(payer, loader, real_ctx).pv
    pv_r = _price(recv, loader, real_ctx).pv
    assert abs(pv_p - pv_r) / pv_p < 1e-3, f"ATM asymmetry: {pv_p:,.2f} vs {pv_r:,.2f}"


@pytest.mark.needs_market_data
def test_bermudan_at_least_european(loader, real_ctx):
    """A Bermudan receiver swaption must be worth >= the co-terminal European
    receiver it contains (more exercise rights cannot reduce value)."""
    berm = BermudanSwaptionTrade("BGE_B", "2x5 berm recv", "USD", NOTIONAL,
                                 direction=SwapDirection.RECEIVER)
    euro = EuropeanSwaptionTrade("BGE_E", "2x5 euro recv", "USD", NOTIONAL,
                                 direction=SwapDirection.RECEIVER,
                                 option_tenor="2Y", swap_tenor="5Y")
    pv_b = _price(berm, loader, real_ctx).pv
    pv_e = _price(euro, loader, real_ctx).pv
    assert pv_b >= pv_e - 1.0, f"Bermudan {pv_b:,.2f} < European {pv_e:,.2f}"


@pytest.mark.needs_market_data
def test_swaption_value_monotonic_in_vol(adapter, loader, real_ctx, real_curve):
    """Higher normal vol must raise the (long) swaption value."""
    from src.pricer.input.common.enums import MarketDataKind
    from src.pricer.input.common.market_data import FlatNormalVolData, MarketDataSet

    trade = EuropeanSwaptionTrade("MONO", "5x10 payer", "USD", NOTIONAL)

    def pv_at(vol_level):
        mds = MarketDataSet(valuation_date=VALUATION_DATE)
        for req in trade.market_data_requirements(real_ctx).requirements:
            if req.kind == MarketDataKind.OIS_CURVE:
                mds.put(req, real_curve, VALUATION_DATE)
            else:
                mds.put(req, FlatNormalVolData("USD", vol_level, VALUATION_DATE), VALUATION_DATE)
        return get_pricer(trade).price(trade, mds, real_ctx).pv

    assert pv_at(0.0050) < pv_at(0.0090) < pv_at(0.0150)


# --------------------------------------------------------------------------- #
# 4. Risk — analytic DV01 vs finite-difference curve bump
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_swap_dv01_matches_finite_difference(loader, real_ctx, real_curve, real_vol):
    trade = OvernightIndexedSwapTrade("DV01", "10Y payer", "USD", NOTIONAL, fixed_rate=0.038)
    base = _price(trade, loader, real_ctx)
    dv01 = base.measures["dv01"]

    bumped = ParOISCurveData(
        currency=real_curve.currency, index=real_curve.index, as_of_date=real_curve.as_of_date,
        tenors=real_curve.tenors,
        par_rates=tuple(r + 1e-4 for r in real_curve.par_rates),
        source="bumped",
    )
    mds = mock_resolve(trade, real_ctx, bumped, real_vol)
    pv_bumped = get_pricer(trade).price(trade, mds, real_ctx).pv
    fd_dv01 = abs(pv_bumped - base.pv)
    assert abs(fd_dv01 - dv01) / dv01 < 0.03, f"DV01 analytic {dv01:,.0f} vs FD {fd_dv01:,.0f}"


# --------------------------------------------------------------------------- #
# 5. Golden PV regression on real data
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
@pytest.mark.parametrize("trade", [
    OvernightIndexedSwapTrade("TRADE_22", "10Y SOFR payer IRS", "USD", NOTIONAL, fixed_rate=0.038),
    EuropeanSwaptionTrade("TRADE_23", "5x10 European payer swaption", "USD", NOTIONAL),
    BermudanSwaptionTrade("TRADE_24", "2x5 Bermudan receiver swaption", "USD", NOTIONAL),
    CapTrade("TRADE_25", "1Y SOFR cap", "USD", NOTIONAL),
], ids=lambda t: t.trade_id)
def test_golden_pv_real_data(trade, loader, real_ctx):
    pv = _price(trade, loader, real_ctx).pv
    expected = GOLDEN_PV[trade.trade_id]
    assert pv == pytest.approx(expected, rel=1e-3), f"{trade.trade_id}: {pv:,.2f} vs {expected:,.2f}"
