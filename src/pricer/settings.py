"""Runtime settings (P5) — environment & operational config, separate from model params.

Two distinct config planes:
  * MODEL parameters (vols, recovery, spreads, scenarios) live in the governed,
    change-controlled `pricer_config.json` (+ optional per-env overlay) — see config.py.
  * RUNTIME / operational config (which environment, log format, worker count, secrets)
    lives HERE, sourced from `PRICER_*` environment variables (and an optional .env),
    so the same artifact promotes across dev/uat/prod by changing env, not code.

Secrets are read from the environment as `SecretStr` and never committed (.env is
gitignored). The pricing module needs only the service API key today. Market-data source
keys (`FRED_API_KEY`, `OPENFIGI_API_KEY`, …) are UN-prefixed and live in their own settings
class (`src/marketdata/common/settings.py`) — not here — because that is what the downloaders
actually read via `load_credentials()`.
"""

from __future__ import annotations

from pydantic import SecretStr

from src.common.config.settings import PlatformSettings


class Settings(PlatformSettings):
    """Pricer runtime settings: the shared env/log/worker fields plus the pricer secret.

    Inherits `env`, `json_logs`, `log_level`, `workers` (all `PRICER_*`) from
    `PlatformSettings`; adds the pricer-side secret below.
    """

    # Service API key (PRICER_API_KEY). When set, the FastAPI facade requires a matching
    # `X-API-Key` header on all routes except /health and /metrics; when unset the check is a
    # no-op (dev parity), so it only enforces once a key is configured.
    api_key: SecretStr | None = None        # PRICER_API_KEY


def get_settings() -> Settings:
    """Load runtime settings from the environment (and .env if present)."""
    return Settings()
