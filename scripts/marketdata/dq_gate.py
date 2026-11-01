"""Post-ingest data-quality gate — the DAG step between market-data download and pricing.

Runs the declared-file checks (presence, row floor, required columns, staleness, nulls) over
`data/marketdata/` and exits non-zero if any check BLOCKs, so an orchestrator fails the
pipeline before a bad snapshot is built. Staleness / null-fraction issues are logged as
warnings and do not fail the run.

    python -m scripts.marketdata.dq_gate
    python -m scripts.marketdata.dq_gate --config configs/marketdata/dq_gate.json --as-of 2026-05-29
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common import storage
from src.common.obs.log import configure_logging
from src.common.quality.gate import DataQualityGate, load_gate_config

logger = logging.getLogger("marketdata.dq_gate")


def main() -> int:
    p = argparse.ArgumentParser(description="Market-data post-ingest data-quality gate")
    p.add_argument("--config", default=str(_ROOT / "configs" / "marketdata" / "dq_gate.json"))
    p.add_argument("--as-of", default=None, help="ISO date for staleness (default: today)")
    args = p.parse_args()

    configure_logging(json_logs=False, level=logging.INFO)
    cfg = load_gate_config(Path(args.config))
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    # Storage location, not a raw path: resolves to <repo>/data/marketdata locally and to
    # <PRICER_DATA_URI>/marketdata in AWS mode, so the gate polices whatever the ingest wrote.
    md_base = storage.join(storage.data_base(_ROOT), "marketdata")
    gate = DataQualityGate(md_base, cfg, as_of=as_of)
    results = gate.run()

    blocks = [r for r in results if r.severity == "block"]
    warns = [r for r in results if r.severity == "warn"]
    for r in warns:
        logger.warning("WARN  %-40s %-16s %s", r.file, r.check, r.message)
    for r in blocks:
        logger.error("BLOCK %-40s %-16s %s", r.file, r.check, r.message)

    n_files = len({r.file for r in results})
    logger.info("DQ gate: %d files checked | %d block | %d warn -> %s",
                n_files, len(blocks), len(warns), "FAIL" if blocks else "PASS")
    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
