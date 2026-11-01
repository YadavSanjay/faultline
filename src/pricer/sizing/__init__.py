"""Risk-appetite position sizing (portfolio construction).

Replaces the flat ~$50mm placeholder position sizes in ``portfolio.json`` with sizes
derived from a risk-appetite mandate. Three profiles — balanced, aggressive growth and
highly-leveraged/risky — differ by VaR budget, gross leverage, risk concentration, a
return tilt and how much of the hedge book they carry.

The engine reuses the existing risk stack: each trade's first-order sensitivities
(``risk.sensitivities``) are projected onto the historical factor-move panel the VaR
engine already builds (``risk.var``), giving a per-trade daily P&L matrix whose sample
covariance is an empirically-diversified trade-level Sigma. Position sizes are then solved
against that Sigma, an expected-return vector and the profile's constraints, and finally
scaled so the book's 1-day 99% VaR matches the profile's risk budget on the capital base.
"""

from .trade_risk import TradeRisk, TradeRiskModel

__all__ = ["TradeRisk", "TradeRiskModel"]
