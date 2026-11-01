"""QuantLib interaction layer.

Consumes the pure-domain objects from `src/pricer/input` plus a resolved
`MarketDataSet`, builds QuantLib curves/vols/instruments, and returns a
`PricingResult`. This is the only layer that imports QuantLib.
"""
