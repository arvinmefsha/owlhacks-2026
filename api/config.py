"""Settings, loaded from the repo-root .env file (see .env.example) or the environment.

Secrets are SecretStr so they print as '**********' if a settings object is ever logged.
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent
REPO_ROOT = API_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-flash"
    database_url: SecretStr
    presage_api_key: SecretStr | None = None
    upload_dir: Path = API_DIR / "uploads"
    max_upload_mb: int = 200
    yolo_detector_model: str = "yolo11n.pt"
    yolo_pose_model: str = "yolo11m-pose.pt"
    yolo_device: str | None = None
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_model: str = "eleven_flash_v2_5"
    live_pose_model: str = "yolo11n-pose.pt"
    live_pose_size: int = 480

    @field_validator("gemini_api_key", "database_url", "elevenlabs_api_key")
    @classmethod
    def _not_placeholder(cls, value: SecretStr | None, info: ValidationInfo) -> SecretStr | None:
        if value is None:
            return None
        secret = value.get_secret_value().strip()
        if not secret or secret.startswith("your-") or "your-password" in secret:
            raise ValueError("missing or still the placeholder from .env.example")
        if info.field_name == "database_url":
            parsed = urlsplit(secret)
            if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.password:
                raise ValueError("DATABASE_URL must be a PostgreSQL URL with a host and password")
        return SecretStr(secret)

    @field_validator("presage_api_key", "yolo_device", "elevenlabs_api_key", "elevenlabs_voice_id", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("elevenlabs_model", "live_pose_model", "live_pose_size", mode="before")
    @classmethod
    def _blank_is_default(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value


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
