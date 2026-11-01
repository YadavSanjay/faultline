"""Input Data Loader.

Receives a trade's `MarketDataRequest` (the list of market-data requirements it
declared) and resolves each one against the CSV store in `data/marketdata/`,
returning a `MarketDataSet`. Scans for the latest snapshot on/before the
valuation date and raises staleness warnings when the freshest available data is
older than the configured tolerance.
"""
