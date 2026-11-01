"""Option-implied signal consumers for scenariogen (market-intelligence B4).

Bridges the marketdata option-signal derivation (B1-B3) into scenario generation: a per-name
equity-vol level, a market-stress severity multiplier, and — the headline — a forward-looking
plausibility check that grades a scenario's equity moves against the market-implied risk-neutral
tail probabilities (signal 5). All additive and mode-(ii)/(iii): no new covariance factor.
"""

from .provider import OptionSignalProvider

__all__ = ["OptionSignalProvider"]
