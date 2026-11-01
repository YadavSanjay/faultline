"""
Stress-scenario calibration market-data pipeline runner.

Usage:
    python -m src.marketdata.stress.runner
    python -m src.marketdata.stress.runner --sources fred_macro move_yahoo
    python -m src.marketdata.stress.runner --sources eia_power

Available sources: fred_macro  move_yahoo  eia_power  gpr  epu  fred_throughput  fred_long

Pulls the stress-SPECIFIC inputs for Methodology B (AI capex boom/bust, §4.9):
  - fred_macro : IG/HY corp OAS + VIX close (credit + equity-vol shock scalars)
  - move_yahoo : ^MOVE rates-vol index (swaption/cap vega shock)
  - eia_power  : EIA datacenter-grid demand (independent capex corroboration)

Capex (EDGAR XBRL) is in the reference pipeline; equity prices + CBOE VIX/skew in
equity/options; single-name CDS in credit; UST curve in bonds. Methodology A
(historical replay) + BIS FX/funding history are a later stage (see config notes).
See docs/design/AITrade_quantlib_portfolio_marketdata.md §2.8, §4.9.
"""

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.marketdata.common.config_loader import load_config
from src.marketdata.common.observability import begin_run, record_run
from src.marketdata.stress.eia_power_downloader import EIAPowerDownloader
from src.marketdata.stress.fred_long_downloader import FredLongDownloader
from src.marketdata.stress.fred_macro_downloader import FREDMacroDownloader, FREDThroughputDownloader
from src.marketdata.stress.geopolitical_downloader import EPUDownloader, GPRDownloader
from src.marketdata.stress.move_downloader import MOVEDownloader


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("stress")
    logger = logging.getLogger(__name__)
    creds = _load_credentials()
    config = load_config("stress")

    available: dict[str, type] = {
        "fred_macro": FREDMacroDownloader,
        "move_yahoo": MOVEDownloader,
        "eia_power":  EIAPowerDownloader,
        "gpr":        GPRDownloader,
        "epu":        EPUDownloader,
        "fred_throughput": FREDThroughputDownloader,
        "fred_long": FredLongDownloader,
    }

    chosen = sources or list(available.keys())
    logger.info("Stress pipeline starting. Sources: %s", chosen)

    results: dict[str, str] = {}
    for name in chosen:
        if name not in available:
            logger.warning("Unknown source %r (valid: %s)", name, list(available))
            continue
        if not config["sources"].get(name, {}).get("enabled", True):
            logger.info("Source %r disabled in config -- skipping", name)
            results[name] = "skipped (disabled)"
            continue

        logger.info("=" * 60)
        logger.info("Starting: %s", name.upper())
        try:
            downloader = available[name](config, creds)
            downloader.run()
            results[name] = "ok"
        except Exception as exc:
            logger.exception("Source %r failed: %s", name, exc)
            results[name] = f"FAILED: {exc}"

    logger.info("=" * 60)
    logger.info("Stress pipeline complete. Results:")
    for src, status in results.items():
        logger.info("  %-12s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress-scenario calibration downloader")
    parser.add_argument(
        "--sources", nargs="+",
        choices=["fred_macro", "move_yahoo", "eia_power", "gpr", "epu", "fred_throughput", "fred_long"],
        default=None,
        help="Sources to run (default: all enabled)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(sources=args.sources)
