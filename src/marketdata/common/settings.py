"""Market-data runtime secrets — API keys sourced from the environment as SecretStr.

Centralizes the source API keys the downloaders need (previously read ad hoc with
`load_dotenv` + `os.getenv` in each runner). Keys are typed `SecretStr` so a logged settings
object never leaks them; they read from un-prefixed environment variables (`FRED_API_KEY`,
`STOOQ_API_KEY`, …) or a gitignored `.env`. In K8s the same names are injected via Secrets,
so no `.env` is needed in production.

`load_credentials()` returns the plain `dict[str, str]` the downloaders expect (a superset is
harmless — each downloader reads only the keys it uses).
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarketdataSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    fred_api_key: SecretStr | None = None           # FRED_API_KEY
    stooq_api_key: SecretStr | None = None           # STOOQ_API_KEY
    finra_api_key: SecretStr | None = None           # FINRA_API_KEY
    finra_api_key_secret: SecretStr | None = None    # FINRA_API_KEY_SECRET
    eia_api_key: SecretStr | None = None             # EIA_API_KEY
    openfigi_api_key: SecretStr | None = None        # OPENFIGI_API_KEY
    faultline_contact_email: str | None = None       # FAULTLINE_CONTACT_EMAIL (SEC/GPR/EPU User-Agent)


def _plain(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""


def load_credentials() -> dict[str, str]:
    """Resolve all source API keys from the environment into the downloaders' creds dict."""
    s = MarketdataSettings()
    return {
        "FRED_API_KEY": _plain(s.fred_api_key),
        "STOOQ_API_KEY": _plain(s.stooq_api_key),
        "FINRA_API_KEY": _plain(s.finra_api_key),
        "FINRA_API_KEY_SECRET": _plain(s.finra_api_key_secret),
        "EIA_API_KEY": _plain(s.eia_api_key),
        "OPENFIGI_API_KEY": _plain(s.openfigi_api_key),
        "FAULTLINE_CONTACT_EMAIL": s.faultline_contact_email or "",
    }
