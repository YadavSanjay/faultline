"""Calculation-input layer: pure domain objects, free of any QuantLib dependency.

Built with object composition + inheritance so the same objects can be driven by
mock data (unit tests) or by the real Input Data Loader (production).
"""
