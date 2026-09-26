"""Settings, loaded from the repo-root .env file (see .env.example) or the environment.

Secrets are SecretStr so they print as '**********' if a settings object is ever logged.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent
REPO_ROOT = API_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: SecretStr
    gemini_model: str = "gemini-3.8-flash"
    database_url: SecretStr
    presage_api_key: SecretStr | None = None
    upload_dir: Path = API_DIR / "uploads"
    max_upload_mb: int = 200

    @field_validator("gemini_api_key", "database_url")
    @classmethod
    def _not_placeholder(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()
        if not secret or secret.startswith("your-") or "your-password" in secret:
            raise ValueError("missing or still the placeholder from .env.example")
        return SecretStr(secret)

    @field_validator("presage_api_key", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # Only report which variables are wrong: pydantic's own message would echo their values.
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors()})
        raise SystemExit(
            f"Missing or invalid settings: {', '.join(names)}. "
            f"Copy .env.example to .env in {REPO_ROOT} and fill them in."
        ) from None
