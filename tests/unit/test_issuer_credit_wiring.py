"""Unit tests for issuer-credit-factors wiring (factor history, day shocks, scenario vectors, bounds).

The locked spec tests cover the pure functions; these cover how a run uses them: the committed config,
``FactorHistory`` building ``credit:<ISSUER>`` from DTCC prints (synthetic files under tmp_path), the
historical-VaR day shock, the scenario vector transform and the plausibility bound.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from src.pricer.input.common.market_data import CdsSpreadData, CreditSpreadData
from src.pricer.risk.factor_history import DayShock, FactorHistory
from src.scenariogen.factor_vector import vector_to_transforms
from src.scenariogen.plausibility import factor_class, hard_bound

D = date(2026, 10, 6)


def _apply(transforms, item):
    for tf in transforms:
        item = tf(item)
    return item


def _items():
    return [CreditSpreadData(issuer="ORCL", currency="USD", spread=0.02, as_of_date=D,
                             curve_points=((5.0, 0.02), (10.0, 0.025))),
            CreditSpreadData(issuer="AAPL", currency="USD", spread=0.006, as_of_date=D),
            CdsSpreadData(reference="ORCL", currency="USD", spread=0.02, recovery=0.4, as_of_date=D),
            CdsSpreadData(reference="CDX.NA.IG", currency="USD", spread=0.0055, recovery=0.4, as_of_date=D)]


def test_committed_config_lists_issuers_with_cds_name_filters():
    cfg = json.loads(Path("configs/pricer/pricer_config.json").read_text(encoding="utf-8"))
    icfg = cfg["risk"]["hist_var"]["issuer_credit"]
    assert icfg["issuers"] == ["ORCL", "META"]
    for issuer in icfg["issuers"]:
        assert cfg["credit"]["references"][issuer]["names"]


def test_scenario_vector_issuer_key_overrides_credit_for_that_issuer():
    tfs = vector_to_transforms({"credit": 0.004, "credit:ORCL": 0.015})
    orcl_bond, aapl_bond, orcl_cds, cdx = (_apply(tfs, i) for i in _items())
    assert orcl_bond.spread == pytest.approx(0.035)
    assert orcl_bond.curve_points == (pytest.approx((5.0, 0.035)), pytest.approx((10.0, 0.04)))
    assert orcl_cds.spread == pytest.approx(0.035)
    assert aapl_bond.spread == pytest.approx(0.010)            # no issuer key: the common credit move
    assert cdx.spread == pytest.approx(0.0095)
    only_issuer = vector_to_transforms({"credit:ORCL": 0.01})
    assert _apply(only_issuer, _items()[1]).spread == pytest.approx(0.006)   # no credit key: others unmoved


def test_day_shock_applies_issuer_moves_over_the_credit_move():
    shock = DayShock(date="2026-10-01", ir_points_years=(), ir_points_shift=(), equity_by_name={},
                     equity_systematic=0.0, equity_vol_pts=0.0, rates_vol_rel=0.0, fx_by_ccy={},
                     credit_shift=0.0005, fxrate_by_ccy={}, equity_vol_term=0.0, equity_vol_skew=0.0,
                     credit_by_issuer={"ORCL": 0.003})
    tfs = shock.transforms()
    assert _apply(tfs, _items()[0]).spread == pytest.approx(0.023)
    assert _apply(tfs, _items()[1]).spread == pytest.approx(0.0065)


def test_issuer_keys_share_the_credit_bound():
    assert factor_class("credit:ORCL") == "credit"
    assert hard_bound({"credit": {"max_abs_bp": 700}}, "credit:META") == pytest.approx(0.07)


def _write(root: Path, prints: list[tuple[str, str, float, str]]) -> None:
    md = root / "data" / "marketdata"
    (md / "credit").mkdir(parents=True)
    (md / "stress").mkdir(parents=True)
    pd.DataFrame([{"date": d, "Action type": "NEWT", "Event type": "TRAD", "Underlying Asset Name": n,
                   "Spread-Leg 1": s, "Expiration Date": exp} for d, n, s, exp in prints]).to_csv(
        md / "credit" / "dtcc_single_name_cds.csv", index=False)
    days = pd.bdate_range("2026-01-01", "2026-03-31")
    pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "BAMLC0A0CM": 0.80}).to_csv(
        md / "stress" / "fred_stress_macro.csv", index=False)


def _fh(root: Path, issuers: list[str]) -> FactorHistory:
    cfg = {"risk": {"hist_var": {"issuer_credit": {"issuers": issuers, "max_gap_days": 60}}},
           "credit": {"references": {"ORCL": {"names": ["ORACLE"], "exclude": ["BIDCO"]}}}}
    return FactorHistory(root, cfg)


def test_factor_history_builds_the_issuer_series_with_backfill_and_zero_fill(tmp_path):
    _write(tmp_path, [("2026-03-02", "Oracle Corporation", 0.0100, "2031-03-20"),
                      ("2026-03-02", "ORACLE BIDCO LLC", 0.0900, "2031-03-20"),     # excluded name
                      ("2026-03-04", "ORACLE CORP", 0.0120, "2031-03-20"),
                      ("2026-03-04", "Oracle Corporation", 0.0500, "2027-03-20"),   # 1Y: out of bucket
                      ("2026-03-09", "Oracle Corporation", 0.0130, "2031-03-20")])
    proxy = pd.Series(0.0001, index=pd.bdate_range("2026-02-02", "2026-03-31"))
    fh = _fh(tmp_path, ["ORCL"])
    s = fh._issuer_credit(proxy)["credit:ORCL"]
    beta = 0.0100 / 0.0080                                         # issuer 100bp / IG 80bp at the splice
    assert s.loc["2026-02-27"] == pytest.approx(0.0001 * beta)     # before DTCC: proxy x beta
    assert s.loc["2026-03-03"] == pytest.approx(0.0)               # forward-filled day
    assert s.loc["2026-03-04"] == pytest.approx(0.0020)
    assert s.loc["2026-03-09"] == pytest.approx(0.0010)
    assert s.loc["2026-03-31"] == pytest.approx(0.0)               # after the last print: unchanged
    assert s.loc["2026-03-03":].sum() == pytest.approx(0.0030)     # 100bp -> 130bp
    assert fh.issuer_credit_info["ORCL"]["beta"] == pytest.approx(beta)
    assert _fh(tmp_path, [])._issuer_credit(proxy) == {}
