"""Shared observability SDK: structured logging, metrics, tracing, run manifests.

Promoted from the pricer module so all four sub-systems emit the same operational signals
— queryable JSON logs carrying a run_id, Prometheus metrics namespaced per module, and a
per-run audit manifest — giving one uniform picture of "did the job run, how long, did it
fail" across the platform. See `docs/design/AITrade_production_hardening_design.md` item 1.
"""
