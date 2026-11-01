"""Retention / garbage-collection for the scenariogen per-run stores.

Prunes the time-growing stores (scenario-result rows, stress report JSONs, run manifests)
older than --keep-days. Dry-run by default; pass --apply to delete. The content-addressed
version store and the sign-off audit log are intentionally NOT touched — old stress reports
reference them, so deleting them would break reproducibility.

    python -m scripts.scenariogen.gc                      # dry-run, 365-day window
    python -m scripts.scenariogen.gc --keep-days 180 --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common.obs.log import configure_logging
from src.common.store.retention import prune_by_age

logger = logging.getLogger("scenariogen.gc")

# (label, path relative to project root, glob) — high-churn, non-reference stores only.
_TARGETS = [
    ("scenario results", "data/pricer/store/scenario_results", "**/*.parquet"),
    ("stress reports", "data/pricer/scenario", "**/scenario_*.json"),
    ("run manifests", "data/scenariogen/store/runs", "**/*.parquet"),
]


def main() -> int:
    p = argparse.ArgumentParser(description="scenariogen store retention / GC")
    p.add_argument("--keep-days", type=int, default=365, help="retain files newer than this")
    p.add_argument("--apply", action="store_true", help="delete (default: dry-run, report only)")
    args = p.parse_args()

    configure_logging(json_logs=False, level=logging.INFO)
    dry_run = not args.apply
    total = 0
    for label, rel, pattern in _TARGETS:
        r = prune_by_age(_ROOT / rel, keep_days=args.keep_days, pattern=pattern, dry_run=dry_run)
        logger.info("%-18s %s: %d/%d removable", label, rel, r.removed, r.scanned)
        total += r.removed
    logger.info("GC (%s, keep_days=%d): %d file(s) %s",
                "dry-run" if dry_run else "APPLIED", args.keep_days, total,
                "would be removed" if dry_run else "removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
