"""Crowding market-data pipeline runner (market-intelligence F5, P7).

    python -m src.marketdata.crowding.runner                          # all enabled sources
    python -m src.marketdata.crowding.runner --sources short_interest
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
from src.marketdata.crowding.short_interest_downloader import ShortInterestDownloader


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("crowding")
    logger = logging.getLogger(__name__)
    creds = _load_credentials()
    config = load_config("crowding")

    available: dict[str, tuple[type, str]] = {
        "short_interest": (ShortInterestDownloader, "sources.finra_short_interest"),
    }
    chosen = sources if sources else list(available.keys())
    logger.info("Crowding pipeline starting. Sources: %s", chosen)

    results: dict[str, str] = {}
    for name in chosen:
        if name not in available:
            logger.warning("Unknown source %r (valid: %s)", name, list(available))
            continue
        cls, cfg_path = available[name]
        if not config["sources"].get(cfg_path.split(".")[1], {}).get("enabled", True):
            logger.info("Source %r disabled — skipping", name)
            results[name] = "skipped (disabled)"
            continue
        logger.info("=" * 60)
        logger.info("Starting: %s", name.upper())
        try:
            cls(config, creds).run()
            results[name] = "ok"
        except Exception as exc:
            logger.exception("Source %r failed: %s", name, exc)
            results[name] = f"FAILED: {exc}"

    logger.info("=" * 60)
    for src, status in results.items():
        logger.info("  %-16s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crowding / positioning market-data downloader")
    p.add_argument("--sources", nargs="+", choices=["short_interest"], default=None,
                   help="Which sources to run (default: all enabled)")
    return p.parse_args()


if __name__ == "__main__":
    run(sources=_parse_args().sources)
