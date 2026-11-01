"""P0b market-data snapshot tests: content-addressing, manifest, caching, drift.

The snapshot pins the exact input bytes that produced a run and stamps a
`snapshot_id` into every result. The most important guard here is
`test_no_reads_outside_declared_inputs`: it proves the adapters never read a file
the snapshot doesn't fingerprint, so `snapshot_id` always reflects the true inputs.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.market_data_loader import MarketDataLoader
from src.pricer.loader.snapshot import MarketDataSnapshot
from src.pricer.portfolio.portfolio_loader import load_portfolio

# --------------------------------------------------------------------------- #
# Content-addressing on a synthetic tree (no real data needed)
# --------------------------------------------------------------------------- #
_VALID_PENSFORD = "as_of_date,settlement_date,forward_sofr\n2026-05-29,2026-05-29,0.040\n"


def _seed(root, pensford=_VALID_PENSFORD):
    md = root / "data" / "marketdata"
    (md / "rates").mkdir(parents=True)
    (md / "rates" / "pensford_sofr_forward.csv").write_text(pensford, encoding="utf-8")
    return {"rates": {"pensford_file": "pensford_sofr_forward.csv"}}


def test_snapshot_id_is_deterministic_and_content_addressed(tmp_path):
    cfg = _seed(tmp_path)
    a = MarketDataSnapshot.build(cfg, tmp_path)
    b = MarketDataSnapshot.build(cfg, tmp_path)
    assert a.snapshot_id == b.snapshot_id
    assert len(a.snapshot_id) == 16

    # change one byte -> different id
    (tmp_path / "data" / "marketdata" / "rates" / "pensford_sofr_forward.csv").write_text(
        _VALID_PENSFORD.replace("0.040", "0.041"), encoding="utf-8")
    c = MarketDataSnapshot.build(cfg, tmp_path)
    assert c.snapshot_id != a.snapshot_id


def test_missing_declared_inputs_are_recorded_not_fatal(tmp_path):
    cfg = _seed(tmp_path)
    snap = MarketDataSnapshot.build(cfg, tmp_path)
    # only the pensford file exists; the other declared defaults are missing
    assert "rates/pensford_sofr_forward.csv" in snap.files
    assert any(m.endswith("treasury_par_yields.csv") for m in snap.missing)


def test_manifest_written_atomically(tmp_path):
    cfg = _seed(tmp_path)
    snap = MarketDataSnapshot.build(cfg, tmp_path)
    # write_manifest now returns a location string (local path or s3:// uri).
    out = Path(snap.write_manifest(tmp_path))
    assert out.name == f"{snap.snapshot_id}.json"
    man = json.loads(out.read_text(encoding="utf-8"))
    assert man["snapshot_id"] == snap.snapshot_id
    assert man["file_count"] == len(snap.files)
    assert {f["path"] for f in man["files"]} == set(snap.files)
    assert not list(out.parent.glob(".*.tmp"))


def test_read_csv_is_cached(tmp_path):
    cfg = _seed(tmp_path)
    snap = MarketDataSnapshot.build(cfg, tmp_path)
    p = tmp_path / "data" / "marketdata" / "rates" / "pensford_sofr_forward.csv"
    first = snap.read_csv(p)
    second = snap.read_csv(p)
    assert first is second  # same cached frame, not re-parsed
    assert isinstance(first, pd.DataFrame)


# --------------------------------------------------------------------------- #
# Real-data coverage / drift guard
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_real_snapshot_has_inputs_and_stamps_mds():
    root = get_project_root()
    config = load_pricer_config()
    loader = MarketDataLoader(config, root)
    assert loader.snapshot.files  # real inputs fingerprinted
    ctx = ValuationContext(valuation_date=date(2026, 6, 17))
    trade = load_portfolio(asset_class="rates")[0]
    mds = loader.resolve(trade.market_data_requirements(ctx), ctx)
    assert mds.snapshot_id == loader.snapshot.snapshot_id


@pytest.mark.needs_market_data
def test_no_reads_outside_declared_inputs():
    """Resolving the whole book must not read any file the snapshot doesn't pin."""
    root = get_project_root()
    config = load_pricer_config()
    loader = MarketDataLoader(config, root)
    ctx = ValuationContext(valuation_date=date(2026, 6, 17))
    for trade in load_portfolio():
        loader.resolve(trade.market_data_requirements(ctx), ctx)
    assert loader.snapshot._read_outside == set(), (
        f"adapters read files outside the declared input set: "
        f"{sorted(loader.snapshot._read_outside)} — update _declared_inputs in snapshot.py"
    )
