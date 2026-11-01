"""QuantLib-based derivative pricing module.

Layers (see docs/design + plan):
  input/   - pure domain objects (NO QuantLib import) describing trades + market-data needs
  loader/  - Input Data Loader: resolves market-data requirements against data/marketdata/ CSVs
  ql/      - QuantLib interaction layer: consumes domain objects, produces PricingResult
  portfolio/ - portfolio configuration + loader
  output/  - result persistence
  runner.py - CLI orchestration (full book / asset class / single trade)
"""
