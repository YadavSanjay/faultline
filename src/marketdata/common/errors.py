"""Shared market-data ingestion errors.

A downloader raises ``MarketDataIngestError`` when a fetch *completes* but yields
too little usable data to trust — e.g. every SEC request 403'd on a bad User-Agent,
or an endpoint moved and every call now 404s. Without this, an empty/degenerate
result is written (or silently skipped) and the pipeline reports success, the same
failure class as the fixed Pensford degenerate-curve bug. The pipeline runners catch
this and mark the source FAILED, so the run surfaces the problem instead of
overwriting good data with nothing.
"""

from __future__ import annotations


class MarketDataIngestError(RuntimeError):
    """A download completed but returned fewer usable records than the floor."""
