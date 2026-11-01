"""Immutable point-in-time market-data snapshot (P0b).

A `MarketDataSnapshot` fingerprints the exact set of market-data files the pricer
consumes and derives a content-addressed `snapshot_id`. Two runs over the same data
get the same id; any byte change anywhere in the inputs changes it. The id is stamped
into every `PricingResult.provenance` (filling the slot P0 reserved), and a manifest
(per-file SHA-256 + size + mtime) is persisted for audit and reproducibility.

The snapshot is also the **single read path**: adapters read CSVs through
`snapshot.read_csv(...)`, which caches parsed frames so the same file is read once per
run instead of once per trade.

Scope note: the input set is the files the pricer actually reads (config-driven +
the option-chain glob), *not* the whole `data/marketdata/` tree — that tree holds large
raw dumps (e.g. the 183 MB DTCC SDR file) the pricer never consumes, and fingerprinting
them would be both slow and misleading about what produced a price.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import storage
from src.common.snapshot import FileFingerprint, compute_snapshot_id, fingerprint_file

from .schema import validate_columns

logger = logging.getLogger(__name__)

__all__ = ["FileFingerprint", "MarketDataSnapshot", "SnapshotReader"]


def _declared_inputs(config: dict, marketdata_dir: str) -> list[str]:
    """Posix relpaths (under data/marketdata) of every file the pricer can consume.

    Mirrors the adapter/config wiring; this is the single source of truth for "what
    are the pricer's inputs". Keep in sync when an adapter gains a new input file —
    the snapshot-coverage test asserts the adapters never read outside this set.
    """
    rates = config.get("rates", {})
    bonds = config.get("bonds", {})
    cash = config.get("cash_equity", {})
    credit = config.get("credit", {})
    fx = config.get("fx", {})
    nv = rates.get("normal_vol", {})

    rels = [
        f"rates/{rates.get('pensford_file', 'pensford_sofr_forward.csv')}",
        f"stress/{nv.get('move_file', 'move_index.csv')}",
        f"bonds/{bonds.get('treasury_file', 'treasury_par_yields.csv')}",
        f"equity/{cash.get('prices_file', 'prices_daily.csv')}",
        f"equity/{cash.get('dividends_file', 'dividends.csv')}",
        f"credit/{credit.get('single_name_file', 'dtcc_single_name_cds.csv')}",
        f"credit/{credit.get('index_file', 'dtcc_index_cds.csv')}",
    ]
    for ccfg in fx.get("currencies", {}).values():
        if ccfg.get("spot_file"):
            rels.append(f"fx/{ccfg['spot_file']}")
        if ccfg.get("rate_file"):
            rels.append(f"fx/{ccfg['rate_file']}")

    chains_dir = storage.join(marketdata_dir, "options", "chains")
    chain_names = sorted(
        loc.replace("\\", "/").rsplit("/", 1)[-1] for loc in storage.glob(chains_dir, "*.csv"))
    rels.extend(f"options/chains/{name}" for name in chain_names)

    return sorted(set(rels))


class MarketDataSnapshot:
    """Content-addressed, immutable view of the pricer's market-data inputs."""

    def __init__(
        self,
        marketdata_dir: str,
        files: dict[str, FileFingerprint],
        missing: list[str],
        snapshot_id: str,
    ):
        self.marketdata_dir = marketdata_dir   # location string (local path or s3:// uri)
        self.files = files          # relpath -> fingerprint
        self.missing = missing      # declared but absent on disk
        self.snapshot_id = snapshot_id
        self._frames: dict[tuple, pd.DataFrame] = {}
        self._read_outside: set[str] = set()  # files read but not declared (drift guard)

    # -- construction -------------------------------------------------------- #
    @classmethod
    def build(cls, config: dict, project_root: Path) -> MarketDataSnapshot:
        marketdata_dir = storage.join(storage.data_base(project_root), "marketdata")
        declared = _declared_inputs(config, marketdata_dir)

        files: dict[str, FileFingerprint] = {}
        missing: list[str] = []
        for rel in declared:
            path = storage.join(marketdata_dir, rel)
            if storage.exists(path):
                files[rel] = fingerprint_file(path, rel)
            else:
                missing.append(rel)

        return cls(marketdata_dir, files, missing, compute_snapshot_id(files))

    # -- single read path ---------------------------------------------------- #
    def read_csv(self, path: Path | str, **kwargs: Any) -> pd.DataFrame:
        """Read a CSV once per run; subsequent reads of the same file/kwargs are cached.

        Returned frames are shared — callers must not mutate them in place (the adapters
        filter/copy, which is safe). Records reads of any file outside the declared input
        set so enumeration drift is caught (see the coverage test).
        """
        loc = str(path)
        key = (self._canonical(loc), tuple(sorted((k, _hashable(v)) for k, v in kwargs.items())))
        if key not in self._frames:
            rel = self._relpath(loc)
            frame = storage.read_csv(loc, **kwargs)
            if rel is not None:
                self._note_read(rel)
                validate_columns(rel, frame)  # data-contract gate (P0c)
            self._frames[key] = frame
        return self._frames[key]

    def _canonical(self, loc: str) -> str:
        """Cache key for a location: resolved absolute path (local) or the URI verbatim."""
        if storage.is_remote_uri(loc):
            return loc
        return str(Path(loc).resolve())

    def _relpath(self, loc: str) -> str | None:
        if storage.is_remote_uri(loc):
            base = self.marketdata_dir.rstrip("/") + "/"
            norm = loc.replace("\\", "/")
            return norm[len(base):] if norm.startswith(base) else None
        try:
            return Path(loc).resolve().relative_to(Path(self.marketdata_dir).resolve()).as_posix()
        except ValueError:
            return None

    def _note_read(self, rel: str) -> None:
        if rel not in self.files and rel not in self._read_outside:
            self._read_outside.add(rel)
            logger.warning(
                "market-data read outside snapshot input set: %s "
                "(snapshot_id will not pin it — update _declared_inputs)", rel
            )

    # -- manifest ------------------------------------------------------------ #
    def manifest(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": datetime.now(UTC).isoformat(),
            "marketdata_dir": str(self.marketdata_dir),
            "file_count": len(self.files),
            "files": [self.files[rel].as_dict() for rel in sorted(self.files)],
            "missing": self.missing,
        }

    def write_manifest(self, project_root: Path) -> str:
        out_dir = storage.join(storage.data_base(project_root), "pricer", "snapshots")
        out = storage.join(out_dir, f"{self.snapshot_id}.json")
        storage.write_text_atomic(out, json.dumps(self.manifest(), indent=2))
        logger.info("Wrote market-data snapshot manifest -> %s", out)
        return out


def _hashable(v: Any) -> Any:
    """Make CSV-reader kwargs (e.g. parse_dates=[...]) usable in a cache key."""
    if isinstance(v, (list, set)):
        return tuple(v)
    return v


class SnapshotReader:
    """Mixin: read CSVs through a `MarketDataSnapshot` when one is attached.

    Adapters constructed with a snapshot (the production path, via `MarketDataLoader`)
    get cached, fingerprinted reads. Adapters constructed without one (some unit tests)
    fall back to plain pandas, so behaviour is unchanged.
    """

    _snapshot: MarketDataSnapshot | None = None

    def _read_csv(self, path: Path | str, **kwargs: Any) -> pd.DataFrame:
        if self._snapshot is not None:
            return self._snapshot.read_csv(path, **kwargs)
        return storage.read_csv(str(path), **kwargs)
