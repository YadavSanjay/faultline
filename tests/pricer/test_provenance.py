"""P0 provenance + atomic-write tests.

Every persisted result must carry enough lineage to reproduce and audit it: a run
id, code version, library versions, config hash, and a market-data snapshot slot.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.output.result_writer import write_result, write_summary
from src.pricer.provenance import RunProvenance, config_hash, git_sha
from src.pricer.ql.common.pricing_result import PricingResult

_RUN_FIELDS = {
    "run_id",
    "git_sha",
    "quantlib_version",
    "python_version",
    "config_hash",
    "priced_at",
}


def test_run_provenance_capture_has_all_fields():
    root = get_project_root()
    prov = RunProvenance.capture(root, load_pricer_config())
    d = prov.as_dict()
    assert set(d) >= _RUN_FIELDS
    assert d["quantlib_version"]  # e.g. "1.42.1"
    assert re.match(r"^\d+\.\d+", d["python_version"])
    # run_id is a uuid4
    assert re.match(r"^[0-9a-f-]{36}$", d["run_id"])
    # priced_at is ISO-8601 UTC
    assert d["priced_at"].endswith("+00:00")


def test_config_hash_is_stable_and_order_independent():
    a = {"x": 1, "y": {"b": 2, "a": 3}}
    b = {"y": {"a": 3, "b": 2}, "x": 1}
    assert config_hash(a) == config_hash(b)
    assert config_hash(a) != config_hash({"x": 2})


def test_git_sha_never_raises(tmp_path):
    # Not a git repo -> "unknown", never an exception (provenance must not break runs).
    assert git_sha(tmp_path) == "unknown"
    # Real repo -> a sha (optionally -dirty).
    sha = git_sha(get_project_root())
    assert re.match(r"^[0-9a-f]{7,40}(-dirty)?$", sha) or sha == "unknown"


def test_result_provenance_merges_per_result_fields():
    prov = RunProvenance.capture(get_project_root(), {"k": "v"})
    rp = prov.result_provenance(pricer_class="SwapPricer", market_data_snapshot_id=None)
    assert rp["pricer_class"] == "SwapPricer"
    assert "market_data_snapshot_id" in rp  # slot present even when None (wired in P0b)
    assert set(rp) >= _RUN_FIELDS


def _make_result(prov: dict) -> PricingResult:
    return PricingResult(
        trade_id="TRADE_TEST",
        trade_type="OIS_SWAP",
        currency="USD",
        valuation_date=date(2026, 5, 29),
        pv=123456.78,
        provenance=prov,
    )


def test_write_result_persists_provenance_and_is_atomic(tmp_path: Path):
    prov = RunProvenance.capture(get_project_root(), {"k": "v"})
    rp = prov.result_provenance(pricer_class="SwapPricer")
    out = write_result(_make_result(rp), "rates", "PV", tmp_path)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["provenance"] == rp
    assert payload["asset_class"] == "rates"
    # no leftover temp file from the atomic write
    assert not list(out.parent.glob(".*.tmp"))


def test_write_summary_carries_run_id(tmp_path: Path):
    rows = [{"trade_id": "TRADE_1", "asset_class": "equity", "trade_type": "STOCK",
             "currency": "USD", "pv": 1.0, "n_warnings": 0, "status": "ok"}]
    out = write_summary(rows, "PV", date(2026, 5, 29), tmp_path, run_id="run-xyz")
    text = out.read_text(encoding="utf-8")
    assert "run_id" in text.splitlines()[0]
    assert "run-xyz" in text
    assert not list(out.parent.glob(".*.tmp"))
