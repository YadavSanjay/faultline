"""Shared runtime-settings base — operational config sourced from the environment.

The runtime plane (which environment, log format, worker count, secrets) is separate from
the governed model-parameter files: it is sourced from `PRICER_*` environment variables
(and an optional `.env`), so the same artifact promotes across dev/uat/prod by changing env,
not code. Each module subclasses `PlatformSettings` to add its own typed `SecretStr` keys;
secrets are never logged and `.env` is gitignored. K8s deployments inject the same env vars
via Secrets, so no `.env` is needed in production.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRICER_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "dev"                 # dev | uat | prod  (PRICER_ENV)
    json_logs: bool = False          # PRICER_JSON_LOGS
    log_level: str = "INFO"          # PRICER_LOG_LEVEL
    workers: int = 1                 # PRICER_WORKERS (default parallelism)

    # Deployment mode + data location (dual-mode: local FS or S3). Defaults keep the
    # historical local behavior byte-for-byte: deploy_mode="local" and an unset data_uri
    # resolve the data root to "<repo>/data" exactly as before. See src/common/storage/.
    deploy_mode: str = "local"       # local | aws  (PRICER_DEPLOY_MODE)
    data_uri: str | None = None      # PRICER_DATA_URI: e.g. s3://bucket/prefix (default <repo>/data)
