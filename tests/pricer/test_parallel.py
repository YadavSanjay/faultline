"""P1 parallel-execution test: parallel pricing matches sequential, to the cent.

Spawns a ProcessPoolExecutor (Windows-safe: worker fns are module-level in the
runner, initargs are picklable). Kept to the small rates book so the process
startup overhead stays modest.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.runner import run_to

pytestmark = pytest.mark.needs_market_data

VDATE = date(2026, 5, 29)


def _pvs(rows: list[dict]) -> dict[str, object]:
    return {r["trade_id"]: r["pv"] for r in rows}


def test_parallel_matches_sequential_rates(tmp_path):
    # outputs go to tmp dirs so the test never writes into the real data/pricer store
    seq = run_to(tmp_path / "seq", asset_class="rates", valuation_date=VDATE, workers=1)
    par = run_to(tmp_path / "par", asset_class="rates", valuation_date=VDATE, workers=2)

    assert _pvs(seq) == _pvs(par)                 # identical PVs
    assert {r["status"] for r in par} == {"ok"}   # all priced in the workers
    assert [r["trade_id"] for r in par] == [r["trade_id"] for r in seq]  # stable order
