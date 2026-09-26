import pytest

from config import Settings, get_settings
from db.database import SQL_DIR, split_statements


def test_placeholder_settings_are_rejected_without_echoing_values(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "your-gemini-api-key")
    monkeypatch.setenv("DATABASE_URL", "postgres://tsdbadmin:your-password@example.tsdb.cloud.timescale.com:5432/tsdb")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exc:
            get_settings()
    finally:
        get_settings.cache_clear()
    message = str(exc.value)
    assert "GEMINI_API_KEY" in message and "DATABASE_URL" in message
    assert "your-password" not in message and "tsdbadmin" not in message


def test_secrets_are_masked_in_repr():
    settings = Settings(gemini_api_key="abc123secret", database_url="postgres://u:hunter2@host/db", presage_api_key="  ")
    text = repr(settings) + str(settings.model_dump())
    assert "abc123secret" not in text and "hunter2" not in text
    assert settings.presage_api_key is None


def test_sql_files_split_into_statements():
    schema = split_statements((SQL_DIR / "schema.sql").read_text())
    assert sum(s.startswith("CREATE TABLE") for s in schema) == 6
    timescale = split_statements((SQL_DIR / "timescale.sql").read_text())
    assert timescale[0] == "CREATE EXTENSION IF NOT EXISTS timescaledb"
    assert len(timescale) == 5
    assert all(";" not in s for s in schema + timescale)
