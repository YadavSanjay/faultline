"""Shared platform layer used by every sub-system (marketdata, pricer, risk, scenariogen).

`common` depends on nothing internal — it is the bottom of the import graph — so the three
product packages can all build on one implementation of observability, config, provenance,
storage, and data-quality without import cycles. See
`docs/design/AITrade_production_hardening_design.md` §2.
"""
