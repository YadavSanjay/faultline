"""News & events market-data pipeline runner (market-intelligence P3, workstream A).

Usage:
    python -m src.marketdata.news.runner                     # all enabled sources, in order
    python -m src.marketdata.news.runner --sources cik_map   # A0 only (refresh the ticker→CIK map)
    python -m src.marketdata.news.runner --sources sec gdelt  # A1 + A2

Order matters: cik_map (A0) must run before sec (A1), which reads the CIK map it writes.
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
from src.marketdata.news.cik_map import CikMapDownloader
from src.marketdata.news.gdelt_salience_downloader import GdeltSalienceDownloader
from src.marketdata.news.sec_events_downloader import SecEventsDownloader


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("news")
    logger = logging.getLogger(__name__)
    creds = _load_credentials()
    config = load_config("news")

    # ordered: A0 (cik_map) before A1 (sec), which reads the map
    available: dict[str, tuple[type, str]] = {
        "cik_map": (CikMapDownloader, "sources.sec_company_tickers"),
        "sec": (SecEventsDownloader, "sources.sec_events"),
        "gdelt": (GdeltSalienceDownloader, "sources.gdelt"),
    }
    chosen = sources if sources else list(available.keys())
    logger.info("News pipeline starting. Sources: %s", chosen)

    results: dict[str, str] = {}
    for name in chosen:
        if name not in available:
            logger.warning("Unknown source %r — skipping (valid: %s)", name, list(available))
            continue
        cls, cfg_path = available[name]
        top_key = cfg_path.split(".")[1]
        if not config["sources"].get(top_key, {}).get("enabled", True):
            logger.info("Source %r disabled in config — skipping", name)
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
    logger.info("News pipeline complete:")
    for src, status in results.items():
        logger.info("  %-10s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="News & events market-data downloader")
    p.add_argument("--sources", nargs="+", choices=["cik_map", "sec", "gdelt"], default=None,
                   help="Which sources to run (default: all enabled, in order)")
    return p.parse_args()


if __name__ == "__main__":
    run(sources=_parse_args().sources)
