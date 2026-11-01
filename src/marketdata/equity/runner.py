"""
Cash Equity market-data pipeline runner.

Usage:
    python -m src.marketdata.equity.runner
    python -m src.marketdata.equity.runner --sources yahoo_events

Available sources: stooq (disabled — broken)  yahoo_events (primary)  nasdaq (disabled — backup)
                   yahoo_history (long adjusted-close history from 1990 for the scenario severity anchors;
                   its own file, long_history_prices.csv)

yahoo_events is the primary OHLCV + corporate-actions source; both it and stooq write
prices_daily.csv, but stooq is disabled (its CSV endpoint now returns JS-rendered HTML).
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
from src.marketdata.equity.nasdaq_downloader import NasdaqDownloader
from src.marketdata.equity.stooq_downloader import StooqDownloader
from src.marketdata.equity.yahoo_downloader import YahooDownloader
from src.marketdata.equity.yahoo_history_downloader import YahooHistoryDownloader


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("equity")
    logger = logging.getLogger(__name__)
    creds  = _load_credentials()
    config = load_config("equity")

    available: dict[str, type] = {
        "stooq":          StooqDownloader,
        "nasdaq":         NasdaqDownloader,
        "yahoo_events":   YahooDownloader,
        "yahoo_history":  YahooHistoryDownloader,
    }

    chosen = sources or list(available.keys())
    tickers = [inst["ticker"] for inst in config["portfolio"]]
    logger.info("Equity pipeline starting. Sources: %s  Instruments: %s", chosen, tickers)
    logger.info("Time window: %s -> %s",
                config["time_window"]["start_date"],
                config["time_window"]["end_date"])

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
    logger.info("Equity pipeline complete. Results:")
    for src, status in results.items():
        logger.info("  %-16s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cash equity market-data downloader")
    parser.add_argument(
        "--sources", nargs="+",
        choices=["stooq", "nasdaq", "yahoo_events", "yahoo_history"],
        default=None,
        help="Sources to run (default: all enabled)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(sources=args.sources)
