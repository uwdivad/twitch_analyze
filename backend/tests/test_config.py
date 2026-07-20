from app.core.config import Settings


def test_clickhouse_password_defaults_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)

    settings = Settings(_env_file=None)

    assert settings.clickhouse_password == "twitch_analyze"


def test_explicit_empty_clickhouse_password_stays_empty() -> None:
    settings = Settings(_env_file=None, clickhouse_password="")

    assert settings.clickhouse_password == ""


def test_empty_env_clickhouse_password_stays_empty(monkeypatch) -> None:
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "")

    settings = Settings(_env_file=None)

    assert settings.clickhouse_password == ""


def test_new_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.api_auth_token == ""
    assert settings.transcription_max_concurrent_jobs == 3
    assert settings.openai_timeout_seconds == 60.0
