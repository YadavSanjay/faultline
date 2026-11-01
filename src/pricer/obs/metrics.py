"""Prometheus metrics for the pricer module.

The registry helpers and the metric factory now live in `src/common/obs/metrics.py` so
every module shares one registry and naming scheme; this module only declares pricer's own
metric instances. Names are unchanged (`pricer_trades_total`, …) for dashboard continuity.
"""

from __future__ import annotations

from src.common.obs.metrics import (
    content_type,
    counter,
    exposition,
    gauge,
    histogram,
    write_textfile,
)

# Re-exported registry helpers + this module's own metric instances (below).
__all__ = [
    "BOOK_PV", "DATA_QUALITY", "LAST_RUN_TS", "PRICE_LATENCY", "RUN_DURATION",
    "RUN_TRADES", "TRADES", "content_type", "counter", "exposition", "gauge",
    "histogram", "write_textfile",
]

# Per-trade outcomes (asset_class x status: ok / blocked / failed).
TRADES = counter("pricer", "trades_total", "Trades processed by outcome",
                 ("asset_class", "status"))

# Per-trade pricing latency (seconds), labelled by asset class.
PRICE_LATENCY = histogram(
    "pricer", "price_seconds", "Per-trade pricing latency (s)", ("asset_class",),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# Whole-run wall-clock (seconds).
RUN_DURATION = histogram(
    "pricer", "run_seconds", "Full-run duration (s)",
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

# Data-quality signals: severity = warn | block | drift.
DATA_QUALITY = counter("pricer", "data_quality_total",
                       "Data-quality / drift events", ("severity",))

# Run gauges.
BOOK_PV = gauge("pricer", "book_pv", "Total book PV of the last run", ("valuation_type",))
LAST_RUN_TS = gauge("pricer", "last_run_timestamp_seconds",
                    "Unix timestamp of the last completed run")
RUN_TRADES = gauge("pricer", "run_trades", "Trades in the last run by status", ("status",))
