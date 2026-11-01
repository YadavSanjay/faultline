"""
Bonds market-data pipeline runner.

Usage:
    python -m src.marketdata.bonds.runner
    python -m src.marketdata.bonds.runner --sources treasury_gov edgar
    python -m src.marketdata.bonds.runner --sources finra

Available sources: treasury_gov  treasurydirect  edgar  finra
(dtcc_credits is retired: issuer CDS comes from the credit single-name feed — credit-sdr-dedup)
"""

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.marketdata.bonds.edgar_downloader import EDGARDownloader
from src.marketdata.bonds.finra_downloader import FINRADownloader
from src.marketdata.bonds.treasury_gov_downloader import TreasuryGovDownloader
from src.marketdata.bonds.treasurydirect_downloader import TreasuryDirectDownloader
from src.marketdata.common.config_loader import load_config
from src.marketdata.common.observability import begin_run, record_run


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("bonds")
    logger = logging.getLogger(__name__)
    creds  = _load_credentials()
    config = load_config("bonds")

    available: dict[str, type] = {
        "treasury_gov":   TreasuryGovDownloader,
        "treasurydirect": TreasuryDirectDownloader,
        "edgar":          EDGARDownloader,
        "finra":          FINRADownloader,
    }

    chosen = sources or list(available.keys())
    logger.info("Bonds pipeline starting. Sources: %s", chosen)
    logger.info("Time window: %s -> %s",
                config["time_window"]["start_date"],
                config["time_window"]["end_date"])

    results: dict[str, str] = {}
    for name in chosen:
        if name not in available:
            logger.warning("Unknown source %r (valid: %s)", name, list(available))
            continue
        src_key = name if name in config["sources"] else None
        if src_key and not config["sources"].get(src_key, {}).get("enabled", True):
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
    logger.info("Bonds pipeline complete. Results:")
    for src, status in results.items():
        logger.info("  %-16s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bonds market-data downloader")
    parser.add_argument(
        "--sources", nargs="+",
        choices=["treasury_gov", "treasurydirect", "edgar", "finra"],
        default=None,
        help="Sources to run (default: all enabled)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(sources=args.sources)
