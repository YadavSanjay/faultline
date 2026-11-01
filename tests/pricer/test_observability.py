"""P3 observability tests: structured logging, metrics, run manifest, PV-drift."""

from __future__ import annotations

import json
import logging
from datetime import date

from src.pricer.engine import PricingResult, TradeOutcome
from src.pricer.obs import metrics
from src.pricer.obs.drift import detect_pv_drift
from src.pricer.obs.log import JsonFormatter, bind, current_context, log_context
from src.pricer.obs.run_manifest import RunManifest, RunManifestStore
from src.pricer.output.result_store import ResultStore


# --------------------------------------------------------------------------- #
# Structured logging
# --------------------------------------------------------------------------- #
def test_json_formatter_includes_context():
    with log_context(run_id="r1", trade_id="TRADE_22"):
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "priced", None, None)
        rec.context = current_context()
        out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "priced" and out["level"] == "INFO"
    assert out["run_id"] == "r1" and out["trade_id"] == "TRADE_22"


def test_log_context_is_scoped():
    bind(run_id="base")
    with log_context(trade_id="T1"):
        assert current_context()["trade_id"] == "T1"
    assert "trade_id" not in current_context()      # popped on exit
    assert current_context()["run_id"] == "base"


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def test_metrics_increment_and_expose():
    before = metrics.TRADES.labels(asset_class="rates", status="ok")._value.get()
    metrics.TRADES.labels(asset_class="rates", status="ok").inc()
    after = metrics.TRADES.labels(asset_class="rates", status="ok")._value.get()
    assert after == before + 1
    text = metrics.exposition().decode()
    assert "pricer_trades_total" in text


def test_metrics_textfile_written(tmp_path):
    out = metrics.write_textfile(tmp_path / "m" / "pricer.prom")
    assert out.exists() and "pricer_trades_total" in out.read_text()


# --------------------------------------------------------------------------- #
# Run manifest
# --------------------------------------------------------------------------- #
def test_run_manifest_roundtrip(tmp_path):
    store = RunManifestStore(tmp_path)
    m = RunManifest("2026-05-29", "run1", "PV", "snap1", "pv1", "abc", "1.42.1",
                    "2026-06-20T00:00:00+00:00", "2026-06-20T00:00:05+00:00", 5.0,
                    30, 30, 0, 0, 2, 1, 5.76e8, "ok")
    store.write(m)
    df = store.recent(10)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["run_id"] == "run1" and int(row["n_trades"]) == 30 and row["status"] == "ok"
    assert row["portfolio_version"] == "pv1"


# --------------------------------------------------------------------------- #
# PV-drift
# --------------------------------------------------------------------------- #
def _outcome(tid: str, pv: float) -> TradeOutcome:
    res = PricingResult(tid, "ois_swap", "USD", date(2026, 5, 29), pv)
    return TradeOutcome(tid, "rates", "ois_swap", "USD", "ok", res, [])


def test_pv_drift_alerts_beyond_tolerance(tmp_path):
    store = ResultStore(tmp_path)
    store.write_run([{
        "valuation_type": "PV", "valuation_date": "2026-05-28", "asset_class": "rates",
        "trade_id": "TRADE_22", "trade_type": "ois_swap", "currency": "USD",
        "pv": 1_000_000.0, "status": "ok", "n_warnings": 0, "run_id": "r0",
        "snapshot_id": "s", "git_sha": "a", "quantlib_version": "1.42.1",
        "priced_at": "2026-06-19T00:00:00+00:00", "measures_json": "{}",
    }], "r0")

    # +20% move -> breaches both abs and rel tolerances
    alerts = detect_pv_drift([_outcome("TRADE_22", 1_200_000.0)], store,
                             abs_tol=1000.0, rel_tol=0.02)
    assert len(alerts) == 1 and alerts[0].trade_id == "TRADE_22"
    assert alerts[0].rel_change == abs(0.20) or abs(alerts[0].rel_change - 0.20) < 1e-9

    # tiny move -> no alert
    assert detect_pv_drift([_outcome("TRADE_22", 1_000_500.0)], store,
                           abs_tol=1000.0, rel_tol=0.02) == []

    # unknown trade with no history -> no alert
    assert detect_pv_drift([_outcome("NEW", 999.0)], store) == []
