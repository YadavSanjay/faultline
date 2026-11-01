"""Unit tests for the long-history wiring (long-history-ingest).

The locked spec tests cover parsing, the downloaders' ``collect()`` and the splice; these cover the
file loader and that the committed configs agree with each other (a drift test).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from src.scenariogen.factor_keys import is_issuer_credit
from src.scenariogen.long_history import load_long_history

REPO = Path(__file__).resolve().parents[3]


def _write_tmp_project(root: Path, *, with_data: bool) -> None:
    cfg_dir = root / "configs" / "scenariogen"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "long_history.json").write_text(json.dumps({
        "files": {"prices": "equity/long_history_prices.csv", "fred": "stress/fred_long_history.csv"},
        "min_overlap": 2,
        "factors": {"eq:AVGO": {"ticker": "AVGO", "proxy": "^SOX"}, "ir:DGS10": {"fred": "DGS10"}},
    }))
    if not with_data:
        return
    md = root / "data" / "marketdata"
    (md / "equity").mkdir(parents=True)
    (md / "stress").mkdir(parents=True)
    dates = ["2001-01-02", "2001-01-03", "2001-01-04", "2001-01-05", "2001-01-08"]
    rows = [(d, "^SOX", v) for d, v in zip(dates, [100, 102, 99, 101, 104], strict=True)]
    rows += [(d, "AVGO", v) for d, v in zip(dates[2:], [50, 52, 51], strict=True)]
    pd.DataFrame(rows, columns=["date", "ticker", "adj_close"]).to_csv(
        md / "equity" / "long_history_prices.csv", index=False)
    pd.DataFrame({"date": dates, "DGS10": [5.0, 5.1, 5.05, 5.2, 5.15]}).to_csv(
        md / "stress" / "fred_long_history.csv", index=False)


def test_load_long_history_reads_files_and_mapping(tmp_path, monkeypatch):
    monkeypatch.delenv("PRICER_DATA_URI", raising=False)
    _write_tmp_project(tmp_path, with_data=True)
    series, info = load_long_history(tmp_path)
    assert info["eq:AVGO"]["spliced"] is True             # 2 shared moves >= min_overlap 2
    assert info["eq:AVGO"]["start"] == "2001-01-03"
    assert series["ir:DGS10"].iloc[0] == pytest.approx((5.1 - 5.0) / 100, rel=1e-12)  # exact algebra


def test_load_long_history_without_files_marks_everything_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("PRICER_DATA_URI", raising=False)
    _write_tmp_project(tmp_path, with_data=False)
    series, info = load_long_history(tmp_path)
    assert series == {}
    assert all(i["missing"] for i in info.values())


def test_committed_mapping_is_covered_by_the_configured_sources():
    mapping = json.loads((REPO / "configs/scenariogen/long_history.json").read_text())["factors"]
    equity = json.loads((REPO / "configs/marketdata/equity/equity_marketdata.json").read_text())
    stress = json.loads((REPO / "configs/marketdata/stress/stress_marketdata.json").read_text())
    tickers = {t["ticker"] for t in equity["sources"]["yahoo_history"]["tickers"]}
    fred = set(stress["sources"]["fred_long"]["series"])
    for factor, spec in mapping.items():
        assert (factor.startswith(("eq:", "fx:", "ir:")) or factor in {"credit", "eqvol"}
                or is_issuer_credit(factor)), factor
        if "fred" in spec:
            assert spec["fred"] in fred, factor
        else:
            assert spec["ticker"] in tickers, factor
            if spec.get("proxy"):
                assert spec["proxy"] in tickers, factor
    assert set(equity["sources"]["yahoo_history"]["required"]) <= tickers
    assert set(stress["sources"]["fred_long"]["required"]) <= fred


def test_runners_register_the_new_sources():
    eq = (REPO / "src/marketdata/equity/runner.py").read_text(encoding="utf-8")
    st = (REPO / "src/marketdata/stress/runner.py").read_text(encoding="utf-8")
    assert '"yahoo_history":  YahooHistoryDownloader' in eq and '"yahoo_history"]' in eq
    assert '"fred_long": FredLongDownloader' in st and '"fred_long"]' in st
