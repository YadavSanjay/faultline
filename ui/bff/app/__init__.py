"""Faultline — Backend-for-Frontend (BFF).

A thin FastAPI read layer the React UI consumes. It (a) proxies the existing pricer
service and (b) adds read endpoints over the Parquet/DuckDB stores, config JSON, and
provenance files. Phase 0 ships the foundations: health/metrics proxy, the cross-module
run history (`/system/runs`), and a derived governance posture (`/governance/posture`).

See ui/docs/BUILD_PLAN.md for the full endpoint roadmap.
"""

__all__ = ["__version__"]
__version__ = "0.0.1"
