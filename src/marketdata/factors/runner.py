"""Style-factor market-data pipeline runner (market-intelligence F4).

Usage:
    python -m src.marketdata.factors.runner
    python -m src.marketdata.factors.runner --sources ken_french
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
from src.marketdata.factors.french_factors_downloader import FrenchFactorsDownloader


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("factors")
    logger = logging.getLogger(__name__)
    config = load_config("factors")

    available: dict[str, type] = {"ken_french": FrenchFactorsDownloader}
    chosen = sources or list(available.keys())
    results: dict[str, str] = {}
    for name in chosen:
        if name not in available:
            logger.warning("Unknown source %r (valid: %s)", name, list(available))
            continue
        if not config["sources"].get(name, {}).get("enabled", True):
            results[name] = "skipped (disabled)"
            continue
        try:
            available[name](config, {}).run()
            results[name] = "ok"
        except Exception as exc:
            logger.exception("Source %r failed: %s", name, exc)
            results[name] = f"FAILED: {exc}"

    for src, status in results.items():
        logger.info("  %-16s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Equity style-factor market-data downloader")
    p.add_argument("--sources", nargs="+", choices=["ken_french"], default=None)
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    run(sources=_parse_args().sources)
