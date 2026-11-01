"""Deploy-mode-aware background job execution + a storage-backed job-record store.

See ``docs/design/AITrade_aws_deployment_design.md`` §5. The BFF (``ui/bff/app/jobs.py``) keeps the
whitelist + gating; execution and record persistence live here so they follow ``deploy_mode``.
"""

from __future__ import annotations

from .runner import AwsJobRunner, JobRunner, LocalSubprocessRunner, get_job_runner
from .store import JobRecordStore

__all__ = [
    "AwsJobRunner",
    "JobRecordStore",
    "JobRunner",
    "LocalSubprocessRunner",
    "get_job_runner",
]
