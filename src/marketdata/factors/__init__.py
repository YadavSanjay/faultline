"""Equity style-factor market data (market-intelligence F4).

Ken French daily FF5 + Momentum, ingested into a single `factors/ff_factors.csv` and consumed by
`factor_history` as the `style:MOM` covariance driver (so "momentum crash" becomes an authorable
scenario) and by a style-exposure regression on the book.
"""
