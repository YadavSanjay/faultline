"""The CDS and bond credit adapters price only from price-forming prints (credit-sdr-dedup).

Once the credit files keep every record, lifecycle, clearing and correction records — and prints
disseminated after the valuation date — must not reach the spread medians. A window that holds no
new trade must fall back to the configured spread cleanly (an empty frame used to come back without
columns and raise a KeyError).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from src.pricer.config import load_pricer_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.credit_adapter import CreditMarketDataAdapter

VDATE = date(2026, 10, 5)


def _row(did: str, spread: str, *, action: str = "NEWT", event: str = "TRAD",
         day: str = "2026-10-01", exec_ts: str = "2026-10-01T15:00:00Z") -> dict[str, str]:
    return {"date": day, "Dissemination Identifier": did, "Original Dissemination Identifier": "",
            "Action type": action, "Event type": event, "Event timestamp": exec_ts,
            "Execution Timestamp": exec_ts, "Expiration Date": "2031-12-20",
            "Underlying Asset Name": "ORACLE CORPORATION", "UPI Underlier Name": "",
            "Spread-Leg 1": spread, "Fixed rate-Leg 1": "0.01", "Notional amount-Leg 1": "5,000,000"}


def _adapter(tmp_path, rows: list[dict[str, str]], monkeypatch) -> CreditMarketDataAdapter:
    monkeypatch.delenv("PRICER_DATA_URI", raising=False)
    credit_dir = tmp_path / "data" / "marketdata" / "credit"
    credit_dir.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(credit_dir / "dtcc_single_name_cds.csv", index=False)
    return CreditMarketDataAdapter(load_pricer_config(), tmp_path)


def test_median_uses_only_new_trades(tmp_path, monkeypatch):
    rows = [
        _row("1000000000000000001", "0.0070"),
        _row("1000000000000000002", "0.0072"),
        _row("1000000000000000003", "0.0074"),
        _row("1000000000000000004", "0.0300", action="MODI"),                    # lifecycle
        _row("1000000000000000005", "0.0300", event="CLRG"),                      # clearing
        _row("1000000000000000006", "0.0300", action="CORR", event=""),           # correction
        _row("1000000000000000007", "0.0300", event="NOVA"),                      # novation
        _row("1000000000000000008", "0.0300", day="2026-10-06",
             exec_ts="2026-10-05T15:00:00Z"),                                     # disseminated after VDATE
    ]
    cds, _, _ = _adapter(tmp_path, rows, monkeypatch).cds_spread("ORCL", ValuationContext(valuation_date=VDATE))
    assert cds.from_dtcc
    assert cds.spread == pytest.approx(0.0072, abs=1e-12)  # median of the three new trades, exact


def test_no_new_trades_falls_back_to_config(tmp_path, monkeypatch):
    rows = [_row("2000000000000000001", "0.0300", action="MODI"),
            _row("2000000000000000002", "0.0300", action="TERM", event="ETRM")]
    cds, _, warnings = _adapter(tmp_path, rows, monkeypatch).cds_spread(
        "ORCL", ValuationContext(valuation_date=VDATE))
    assert not cds.from_dtcc
    assert cds.spread == pytest.approx(175 / 1e4, abs=1e-12)  # credit.references.ORCL.fallback_bp
    assert any("fallback" in w.lower() for w in warnings)


def test_bond_issuer_cds_median_uses_only_new_trades(tmp_path, monkeypatch):
    # The bond adapter reads the same single-name file for issuer spreads; its 5Y median must come from
    # new trades only, independently of the CDS adapter (review P3: the real-data test can't catch this).
    from src.pricer.loader.bonds_adapter import BondsMarketDataAdapter

    rows = [
        _row("3000000000000000001", "0.0070"),
        _row("3000000000000000002", "0.0072"),
        _row("3000000000000000003", "0.0074"),
        _row("3000000000000000004", "0.0300", action="MODI"),
        _row("3000000000000000005", "0.0300", event="CLRG"),
        _row("3000000000000000006", "0.0300", action="CORR", event=""),
        _row("3000000000000000007", "0.0300", day="2026-10-06", exec_ts="2026-10-05T15:00:00Z"),
    ]
    monkeypatch.delenv("PRICER_DATA_URI", raising=False)
    credit_dir = tmp_path / "data" / "marketdata" / "credit"
    credit_dir.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(credit_dir / "dtcc_single_name_cds.csv", index=False)
    cfg = load_pricer_config()
    adapter = BondsMarketDataAdapter(cfg, tmp_path)
    ctx = ValuationContext(valuation_date=VDATE)
    cds_bp = adapter._spread_from_single_name_cds("ORCL", ctx, cfg["bonds"]["credit_spread"])
    assert cds_bp == pytest.approx(72.0, abs=1e-9)   # median of 70/72/74 bp, by hand


def test_history_shortfall_is_reported(tmp_path, monkeypatch):
    # Review P2: a file whose first record is later than valuation date - curve lookback must warn.
    rows = [_row("4000000000000000001", "0.0070", day="2026-09-01", exec_ts="2026-09-01T15:00:00Z"),
            _row("4000000000000000002", "0.0072", day="2026-09-02", exec_ts="2026-09-02T15:00:00Z"),
            _row("4000000000000000003", "0.0074", day="2026-09-03", exec_ts="2026-09-03T15:00:00Z")]
    _, _, warnings = _adapter(tmp_path, rows, monkeypatch).cds_spread("ORCL", ValuationContext(valuation_date=VDATE))
    assert any("history starts 2026-09-01" in w for w in warnings)
