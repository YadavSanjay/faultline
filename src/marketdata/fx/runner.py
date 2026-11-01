"""
FX Derivatives market-data pipeline runner.

Usage:
    python -m src.marketdata.fx.runner
    python -m src.marketdata.fx.runner --sources ecb_fx fed_h10
    python -m src.marketdata.fx.runner --sources boj_tonar cbc_twd_spot

Available sources: ecb_fx  fed_h10  boj_tonar  cbc_twd_spot

Covers trade #26 (5Y USD/TWD non-deliverable cross-currency swap) and trade #27
(3M USD/JPY FX forward). FX forward points are CIP-derived at pricing time from
spot + short rates (SOFR from the rates pipeline) — not downloaded here.
See docs/design/AITrade_quantlib_portfolio_marketdata.md §1.5, §2.5, §4.6.
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
from src.marketdata.fx.boj_tonar_downloader import BOJTonarDownloader
from src.marketdata.fx.cbc_twd_downloader import CBCTwdDownloader
from src.marketdata.fx.ecb_fx_downloader import ECBFxDownloader
from src.marketdata.fx.fed_h10_downloader import FedH10Downloader


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("fx")
    logger = logging.getLogger(__name__)
    creds = _load_credentials()
    config = load_config("fx")

    available: dict[str, type] = {
        "ecb_fx":       ECBFxDownloader,
        "fed_h10":      FedH10Downloader,
        "boj_tonar":    BOJTonarDownloader,
        "cbc_twd_spot": CBCTwdDownloader,
    }

    chosen = sources or list(available.keys())
    instruments = [inst["display"] for inst in config["portfolio"]]
    logger.info("FX pipeline starting. Sources: %s", chosen)
    logger.info("Portfolio instruments: %s", instruments)
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
    logger.info("FX pipeline complete. Results:")
    for src, status in results.items():
        logger.info("  %-14s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FX derivatives market-data downloader")
    parser.add_argument(
        "--sources", nargs="+",
        choices=["ecb_fx", "fed_h10", "boj_tonar", "cbc_twd_spot"],
        default=None,
        help="Sources to run (default: all enabled)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(sources=args.sources)
