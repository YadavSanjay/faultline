"""
Credit Derivatives market-data pipeline runner.

Usage:
    python -m src.marketdata.credit.runner
    python -m src.marketdata.credit.runner --sources dtcc_single_name dtcc_index
    python -m src.marketdata.credit.runner --sources cdx_annex

Available sources: dtcc_single_name  dtcc_index  cdx_annex

Covers trades #28 (ORCL CDS), #29 (META CDS), #30 (CDX.NA.IG index).
See docs/design/AITrade_quantlib_portfolio_marketdata.md §1.6, §2.6, §4.7.
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
from src.marketdata.credit.cdx_annex_downloader import CDXAnnexDownloader
from src.marketdata.credit.dtcc_credit_downloader import (
    DTCCIndexDownloader,
    DTCCSingleNameDownloader,
)


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("credit")
    logger = logging.getLogger(__name__)
    creds = _load_credentials()
    config = load_config("credit")

    available: dict[str, type] = {
        "dtcc_single_name": DTCCSingleNameDownloader,
        "dtcc_index":       DTCCIndexDownloader,
        "cdx_annex":        CDXAnnexDownloader,
    }

    chosen = sources or list(available.keys())
    instruments = [inst["display"] for inst in config["portfolio"]]
    logger.info("Credit pipeline starting. Sources: %s", chosen)
    logger.info("Portfolio instruments: %s", instruments)

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
    logger.info("Credit pipeline complete. Results:")
    for src, status in results.items():
        logger.info("  %-18s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Credit derivatives market-data downloader")
    parser.add_argument(
        "--sources", nargs="+",
        choices=["dtcc_single_name", "dtcc_index", "cdx_annex"],
        default=None,
        help="Sources to run (default: all enabled)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(sources=args.sources)
