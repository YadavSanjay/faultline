"""Prometheus metrics — shared registry + a per-module metric factory.

Batch runs are pull-incompatible (they exit), so a runner writes an OpenMetrics textfile
(for the node_exporter textfile collector or a pushgateway) while the long-running service
exposes the same registry at GET /metrics for live scraping.

Each module registers its own metrics through `counter/gauge/histogram(module, name, …)`,
which prefixes the metric name with the module (`marketdata_source_rows_total`,
`risk_var_value`, `scenariogen_limit_breach_total`) so names never clash on the one shared
`REGISTRY`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    write_to_textfile,
)


def counter(module: str, name: str, doc: str,
            labels: Sequence[str] = ()) -> Counter:
    return Counter(f"{module}_{name}", doc, list(labels))


def gauge(module: str, name: str, doc: str,
          labels: Sequence[str] = ()) -> Gauge:
    return Gauge(f"{module}_{name}", doc, list(labels))


def histogram(module: str, name: str, doc: str,
              labels: Sequence[str] = (),
              buckets: Sequence[float] | None = None) -> Histogram:
    full = f"{module}_{name}"
    if buckets is None:
        return Histogram(full, doc, list(labels))
    return Histogram(full, doc, list(labels), buckets=tuple(buckets))


def content_type() -> str:
    return CONTENT_TYPE_LATEST


def exposition() -> bytes:
    """Prometheus text exposition of the default registry (for GET /metrics)."""
    return generate_latest(REGISTRY)


def write_textfile(path: Path) -> Path:
    """Write the registry to an OpenMetrics textfile (atomic, per prometheus_client)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_to_textfile(str(path), REGISTRY)
    return path
