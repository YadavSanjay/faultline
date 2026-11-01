"""Post-ingest data-quality gate.

Runs as an orchestration step between market-data download and snapshot: checks each
declared input file for presence, a row-count floor, required columns, null fraction, and
staleness, classifying each failure as `warn` (record + continue) or `block` (fail the DAG
before a bad snapshot is created). Distinct from the pricer's pricing-time staleness policy,
but shares the same `warn`/`block` vocabulary. See
`docs/design/AITrade_production_hardening_design.md` item 5.
"""
