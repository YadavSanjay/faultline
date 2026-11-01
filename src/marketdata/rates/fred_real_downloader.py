"""FRED real-rate / breakeven downloader (F6, market-intelligence P5).

Pulls the TIPS-implied real 10Y yield and the market-implied inflation compensation via the same
``fredgraph.csv`` path as the nominal rates, but into a **separate** file
(``rates/fred_real_rates.csv``) so these scenariogen-only driver series never enter the
pricing-adjacent ``fred_rates.csv`` fingerprint (snapshot discipline, cf. P0's ``chains_signals/``).

  DFII10   10Y TIPS (real) yield
  T10YIE   10Y breakeven inflation (nominal 10Y - real 10Y)
  T5YIFR   5Y5Y forward inflation expectation

Decomposing a nominal move into real vs breakeven is the FINDING 6 upgrade: a real-rate-driven
move hits long-duration tech *and* long bonds together (2022), a breakeven-driven move does not.
Reuses ``FREDDownloader``'s fetch/merge/upsert; only the ``sources`` key differs.
"""

from ..rates.fred_downloader import FREDDownloader


class FREDRealRatesDownloader(FREDDownloader):
    """Download the FRED real-rate / breakeven series into ``fred_real_rates.csv``."""

    source_key = "fred_real_rates"
