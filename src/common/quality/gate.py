"""Data-quality gate engine — per-file checks over a set of declared input files.

Each declared file is checked for presence, a row-count floor, required columns, null
fraction, and staleness. A failed check is classified `warn` (record + continue) or `block`
(fail the DAG). Structural problems (missing file, zero rows, missing columns) block by
default; staleness and null-fraction warn by default — these are configurable per check and
per file. The gate reads each CSV directly so checks are exact; the file set is the data the
pricer/risk actually consume, so the gate guards the real inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from src.common import storage
from src.common.config.models import Lenient, validate
from src.common.obs import metrics as obs_metrics

# severity = warn | block (ok results are not counted)
DQ_EVENTS = obs_metrics.counter(
    "marketdata", "dq_total", "Data-quality gate failures by severity", ("severity",))

_SEVERITIES = {"warn", "block"}


class FileRule(Lenient):
    """Per-file overrides; any unset field falls back to the gate-level default."""

    required_columns: list[str] = Field(default_factory=list)
    date_col: str | None = None
    min_rows: int | None = None
    max_staleness_days: int | None = None
    max_null_fraction: float | None = None


class DqGateConfig(Lenient):
    default_action: str = "block"        # presence / min_rows / required_columns failures
    staleness_action: str = "warn"
    null_action: str = "warn"
    min_rows: int = 1
    max_staleness_days: int | None = None
    max_null_fraction: float | None = None
    date_col: str = "date"
    files: dict[str, FileRule] = Field(default_factory=dict)


def load_gate_config(path: Path) -> DqGateConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    validate(DqGateConfig, raw, f"dq-gate config {path.name}")
    return DqGateConfig.model_validate(raw)


@dataclass
class CheckResult:
    file: str
    check: str
    severity: str          # ok | warn | block
    message: str = ""

    @property
    def failed(self) -> bool:
        return self.severity in _SEVERITIES

    def as_dict(self) -> dict[str, Any]:
        return {"file": self.file, "check": self.check,
                "severity": self.severity, "message": self.message}


class DataQualityGate:
    """Evaluate the declared files under `base_dir` against a `DqGateConfig`.

    `base_dir` is a **storage location**, not necessarily a filesystem path: in AWS mode it is an
    ``s3://…`` prefix. All access goes through `src.common.storage`, so the gate polices the same
    tree the pipeline actually wrote regardless of `PRICER_DEPLOY_MODE`.
    """

    def __init__(self, base_dir: Path | str, config: DqGateConfig, as_of: date | None = None):
        self.base_dir = str(base_dir)
        self.cfg = config
        self.as_of = as_of or date.today()

    def run(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        for rel in sorted(self.cfg.files):
            results.extend(self._check_file(rel, self.cfg.files[rel]))
        for r in results:
            if r.failed:
                DQ_EVENTS.labels(severity=r.severity).inc()
        return results

    def passed(self, results: list[CheckResult]) -> bool:
        return not any(r.severity == "block" for r in results)

    # -- per-file checks ----------------------------------------------------- #
    def _check_file(self, rel: str, rule: FileRule) -> list[CheckResult]:
        loc = storage.join(self.base_dir, rel)
        if not storage.exists(loc):
            return [CheckResult(rel, "present", self.cfg.default_action, "file is missing")]

        try:
            df = storage.read_csv(loc)
        except Exception as exc:
            return [CheckResult(rel, "readable", self.cfg.default_action, f"unreadable: {exc}")]

        out: list[CheckResult | None] = [CheckResult(rel, "present", "ok")]
        out.append(self._check_rows(rel, rule, df))
        out.extend(self._check_columns(rel, rule, df))
        out.append(self._check_staleness(rel, rule, df))
        out.extend(self._check_nulls(rel, rule, df))
        return [r for r in out if r is not None]

    def _check_rows(self, rel: str, rule: FileRule, df: pd.DataFrame) -> CheckResult:
        floor = rule.min_rows if rule.min_rows is not None else self.cfg.min_rows
        if len(df) < floor:
            return CheckResult(rel, "min_rows", self.cfg.default_action,
                               f"{len(df)} rows < floor {floor}")
        return CheckResult(rel, "min_rows", "ok")

    def _check_columns(self, rel: str, rule: FileRule, df: pd.DataFrame) -> list[CheckResult]:
        if not rule.required_columns:
            return []
        missing = [c for c in rule.required_columns if c not in df.columns]
        if missing:
            return [CheckResult(rel, "required_columns", self.cfg.default_action,
                                f"missing columns: {missing}")]
        return [CheckResult(rel, "required_columns", "ok")]

    def _check_staleness(self, rel: str, rule: FileRule, df: pd.DataFrame) -> CheckResult | None:
        tol = rule.max_staleness_days if rule.max_staleness_days is not None \
            else self.cfg.max_staleness_days
        if tol is None:
            return None
        date_col = rule.date_col or self.cfg.date_col
        if date_col not in df.columns:
            return CheckResult(rel, "staleness", self.cfg.staleness_action,
                               f"no date column {date_col!r} to assess staleness")
        dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if dates.empty:
            return CheckResult(rel, "staleness", self.cfg.staleness_action,
                               "no parseable dates")
        latest = dates.max().date()
        age = (self.as_of - latest).days
        if age > tol:
            return CheckResult(rel, "staleness", self.cfg.staleness_action,
                               f"latest {latest} is {age}d old (tolerance {tol}d)")
        return CheckResult(rel, "staleness", "ok")

    def _check_nulls(self, rel: str, rule: FileRule, df: pd.DataFrame) -> list[CheckResult]:
        tol = rule.max_null_fraction if rule.max_null_fraction is not None \
            else self.cfg.max_null_fraction
        if tol is None or df.empty:
            return []
        cols = rule.required_columns or list(df.columns)
        bad: list[str] = []
        for c in cols:
            if c in df.columns:
                frac = float(df[c].isna().mean())
                if frac > tol:
                    bad.append(f"{c}={frac:.0%}")
        if bad:
            return [CheckResult(rel, "null_fraction", self.cfg.null_action,
                                f"null fraction over {tol:.0%}: {bad}")]
        return [CheckResult(rel, "null_fraction", "ok")]
