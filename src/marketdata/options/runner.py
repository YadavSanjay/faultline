"""
Equity Derivatives market-data pipeline runner.

Usage:
    python -m src.marketdata.options.runner
    python -m src.marketdata.options.runner --sources cboe_chains cboe_vol_indices
    python -m src.marketdata.options.runner --sources yahoo_options

Available sources: cboe_chains  cboe_vol_indices  yahoo_options
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
from src.marketdata.options.cboe_chains_downloader import CBOEChainsDownloader
from src.marketdata.options.cboe_vol_indices_downloader import CBOEVolIndicesDownloader
from src.marketdata.options.yahoo_options_downloader import YahooOptionsDownloader


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("options")
    logger = logging.getLogger(__name__)
    creds  = _load_credentials()
    config = load_config("options")

    available: dict[str, type] = {
        "cboe_chains":      CBOEChainsDownloader,
        "cboe_vol_indices": CBOEVolIndicesDownloader,
        "yahoo_options":    YahooOptionsDownloader,
    }

    chosen = sources or list(available.keys())
    instruments = [inst["display"] for inst in config["portfolio"]]
    logger.info("Options pipeline starting. Sources: %s", chosen)
    logger.info("Portfolio instruments: %s", list(dict.fromkeys(instruments)))

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
    logger.info("Options pipeline complete. Results:")
    for src, status in results.items():
        logger.info("  %-20s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Equity derivatives market-data downloader")
    parser.add_argument(
        "--sources", nargs="+",
        choices=["cboe_chains", "cboe_vol_indices", "yahoo_options"],
        default=None,
        help="Sources to run (default: all enabled)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(sources=args.sources)
