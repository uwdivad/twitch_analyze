import os

import pytest

from app.core.config import Settings, get_settings


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    # The application is asyncio-only (asyncio.to_thread, asyncio.Queue, etc.).
    # Newer anyio versions parametrize tests over every installed backend, and
    # trio is present as a transitive streamlink dependency; pin the suite to
    # asyncio so tests are deterministic across environments.
    return "asyncio"


def _settings_env_var_names() -> set[str]:
    prefix = Settings.model_config.get("env_prefix", "")
    names = {f"{prefix}{field}" for field in Settings.model_fields}
    if Settings.model_config.get("case_sensitive", False):
        return names
    # pydantic-settings matches env vars case-insensitively by default, so clear
    # every spelling present in the environment (API_AUTH_TOKEN, api_auth_token, ...).
    lowered = {name.lower() for name in names}
    return {key for key in os.environ if key.lower() in lowered}


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch):
    # `Settings(_env_file=None)` still reads process env vars, so an exported
    # API_AUTH_TOKEN, OPENAI_API_KEY, ENABLE_* flag, etc. on the developer's machine
    # would otherwise change test outcomes. Tests that need a value set it themselves.
    for name in _settings_env_var_names():
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
