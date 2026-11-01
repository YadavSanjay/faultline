"""
Reference / static market-data pipeline runner.

Usage:
    python -m src.marketdata.reference.runner
    python -m src.marketdata.reference.runner --sources openfigi
    python -m src.marketdata.reference.runner --sources edgar_xbrl

Available sources: openfigi  edgar_xbrl

Builds the canonical instruments.json identifier map (all 30 trades) and the SEC
EDGAR XBRL fundamentals/capex facts used for stress calibration. Holiday
calendars, day-count conventions and ISDA definitions are shipped by QuantLib /
are documents — nothing to download (§2.7).
See docs/design/AITrade_quantlib_portfolio_marketdata.md §2.7, §4.8.
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
from src.marketdata.reference.edgar_xbrl_downloader import EDGARXBRLDownloader
from src.marketdata.reference.openfigi_downloader import OpenFIGIDownloader


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("reference")
    logger = logging.getLogger(__name__)
    creds = _load_credentials()
    config = load_config("reference")

    available: dict[str, type] = {
        "openfigi":   OpenFIGIDownloader,
        "edgar_xbrl": EDGARXBRLDownloader,
    }

    chosen = sources or list(available.keys())
    logger.info("Reference pipeline starting. Sources: %s", chosen)
    logger.info("Portfolio: %d trades", len(config["portfolio"]))

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
    logger.info("Reference pipeline complete. Results:")
    for src, status in results.items():
        logger.info("  %-12s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reference / static market-data downloader")
    parser.add_argument(
        "--sources", nargs="+",
        choices=["openfigi", "edgar_xbrl"],
        default=None,
        help="Sources to run (default: all enabled)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(sources=args.sources)
