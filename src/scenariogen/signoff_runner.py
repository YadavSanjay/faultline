"""Analyst sign-off CLI (S5).

Records an approve/reject decision for a scenario *version* (the S3 content hash), or lists
the sign-off history. The stress runner's `--require-signoff` then admits only approved
versions to a production run.

    # show the current version of each scenario and sensitivity + its sign-off status (replays resolved)
    python -m src.scenariogen.signoff_runner --list

    # approve a scenario's current definition version
    python -m src.scenariogen.signoff_runner --approve taiwan_geopolitical --note "Q3 review"

    # reject a specific version explicitly
    python -m src.scenariogen.signoff_runner --reject taiwan_geopolitical --version <hash>
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.risk.factor_history import FactorHistory
from src.pricer.runner import _setup_logging
from src.scenariogen.catalog import ScenarioCatalog
from src.scenariogen.signoff import SignoffStore, _api_key_safe_user, scenario_versions

logger = logging.getLogger("scenariogen.signoff")


def _catalog(root: Path) -> ScenarioCatalog:
    """The catalog as the stress runner builds it, so replays carry their resolved (registered) versions."""
    return ScenarioCatalog.from_config(FactorHistory(root, load_pricer_config()), project_root=root)


def _current_version(scenario: str, catalog: ScenarioCatalog) -> str:
    versions = scenario_versions(catalog)
    if scenario in versions:
        return versions[scenario]
    if scenario in catalog.errors:
        raise SystemExit(f"scenario {scenario!r} can't be resolved ({catalog.errors[scenario]}); "
                         "pass --version to sign a specific version")
    raise SystemExit(f"unknown scenario {scenario!r}; known: {list(versions)}")


def main() -> None:
    _setup_logging()
    args = _parse_args()
    root = get_project_root()
    store = SignoffStore(root)

    if args.list:
        catalog = _catalog(root)
        versions = scenario_versions(catalog)
        sensitivities = set(catalog.sensitivity_names())
        for name, version in versions.items():
            rec = store.latest(version)
            status = (f"{rec.status} by {rec.approver} @ {rec.recorded_at}" if rec
                      else "(no sign-off)")
            kind = "  [sensitivity]" if name in sensitivities else ""
            logger.info("  %-22s v=%s  %s%s", name, version, status, kind)
        for name, exc in catalog.errors.items():
            logger.warning("  %-22s UNRESOLVED (%s)", name, exc)
        return

    scenario = args.approve or args.reject
    status = "approved" if args.approve else "rejected"
    version = args.version or _current_version(scenario, _catalog(root))
    approver = args.approver or _api_key_safe_user()
    rec = store.record(version, scenario, status, approver, note=args.note or "")
    logger.info("Recorded %s for %s v=%s by %s", rec.status, scenario, version, approver)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="scenariogen analyst sign-off")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", metavar="SCENARIO", help="approve a scenario's version")
    g.add_argument("--reject", metavar="SCENARIO", help="reject a scenario's version")
    g.add_argument("--list", action="store_true", help="list scenarios + sign-off status")
    p.add_argument("--version", default=None, help="explicit scenario_version (default: current)")
    p.add_argument("--approver", default=None, help="approver id (default: OS user)")
    p.add_argument("--note", default=None, help="free-text note")
    return p.parse_args()


if __name__ == "__main__":
    main()
