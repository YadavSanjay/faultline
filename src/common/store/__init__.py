"""Shared storage helpers (retention/GC) for the Parquet/JSON stores under data/.

The per-run stores (scenario results, run manifests, report JSONs) grow unbounded; this
package provides age- and count-based pruning so an operator can cap their footprint.
Reference-critical, content-addressed stores (scenario versions, sign-off audit log) are
NOT pruned by these helpers — deleting them would break reproducibility of old reports.
See `docs/design/AITrade_production_hardening_design.md` item 8.
"""
