"""Model governance & price-quality controls (P4).

Three controls in the SR 11-7 spirit:
- golden.py          frozen-PV regression gate (no silent price changes)
- ipv.py             independent price verification (a second method/identity per trade)
- model_inventory.py governed registry of each pricer's model, assumptions, limitations
"""
