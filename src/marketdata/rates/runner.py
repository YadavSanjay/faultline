"""
Rates market-data pipeline runner.

Usage:
    python -m src.marketdata.rates.runner
    python -m src.marketdata.rates.runner --sources nyfed fred
    python -m src.marketdata.rates.runner --sources dtcc

Available sources: nyfed  fred  pensford  dtcc
"""

import argparse
import logging
import sys
from pathlib import Path

# ---- path setup so script runs from project root or src/ ----------------
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# -------------------------------------------------------------------------

from src.marketdata.common.config_loader import load_config
from src.marketdata.common.observability import begin_run, record_run
from src.marketdata.rates.dtcc_sdr_downloader import DTCCSDRRatesDownloader
from src.marketdata.rates.fred_downloader import FREDDownloader
from src.marketdata.rates.fred_real_downloader import FREDRealRatesDownloader
from src.marketdata.rates.nyfed_acm_downloader import NYFedACMDownloader
from src.marketdata.rates.nyfed_downloader import NYFedDownloader
from src.marketdata.rates.pensford_downloader import PensfordDownloader


def _load_credentials() -> dict[str, str]:
    from src.marketdata.common.settings import load_credentials
    return load_credentials()


def run(sources: list[str] | None = None) -> None:
    handle = begin_run("rates")
    logger = logging.getLogger(__name__)
    creds = _load_credentials()
    config = load_config("rates")

    available: dict[str, tuple[type, str]] = {
        "nyfed":     (NYFedDownloader,          "sources.nyfed_sofr"),
        "fred":      (FREDDownloader,           "sources.fred_rates"),
        "fred_real": (FREDRealRatesDownloader,  "sources.fred_real_rates"),
        "nyfed_acm": (NYFedACMDownloader,       "sources.nyfed_acm"),
        "pensford":  (PensfordDownloader,       "sources.pensford_forward_curve"),
        "dtcc":      (DTCCSDRRatesDownloader,   "sources.dtcc_sdr_rates"),
    }

    chosen = sources if sources else list(available.keys())
    logger.info("Rates pipeline starting. Sources: %s", chosen)
    logger.info("Time window: %s -> %s",
                config["time_window"]["start_date"],
                config["time_window"]["end_date"])

    results: dict[str, str] = {}

    for name in chosen:
        if name not in available:
            logger.warning("Unknown source %r — skipping (valid: %s)", name, list(available))
            continue

        cls, cfg_path = available[name]
        # Check enabled flag in config
        top_key = cfg_path.split(".")[1]
        if not config["sources"].get(top_key, {}).get("enabled", True):
            logger.info("Source %r is disabled in config — skipping", name)
            results[name] = "skipped (disabled)"
            continue

        logger.info("=" * 60)
        logger.info("Starting: %s", name.upper())
        try:
            downloader = cls(config, creds)
            downloader.run()
            results[name] = "ok"
        except Exception as exc:
            logger.exception("Source %r failed: %s", name, exc)
            results[name] = f"FAILED: {exc}"

    logger.info("=" * 60)
    logger.info("Rates pipeline complete. Results:")
    for src, status in results.items():
        logger.info("  %-12s %s", src, status)
    record_run(handle, results)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rates market-data downloader")
    parser.add_argument(
        "--sources", nargs="+",
        choices=["nyfed", "fred", "fred_real", "nyfed_acm", "pensford", "dtcc"],
        default=None,
        help="Which sources to run (default: all enabled sources)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(sources=args.sources)
