"""Risk engine (P2): sensitivities, scenarios/stress, VaR/ES, and P&L attribution.

Built on the pure `PricingEngine.price(trade, mds, ctx)` over the immutable market-data
snapshot (P0b): resolve the base inputs once, then perturb a *copy* of the resolved
`MarketDataSet` and reprice. One methodology — bump-and-revalue — applied consistently
across every asset class, which is what makes the numbers explainable and auditable.
"""
