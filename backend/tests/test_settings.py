import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import routes
from app.core import runtime_settings
from app.core.config import ENV_ONLY_SETTINGS, Settings, get_settings, load_settings, read_overrides
from app.models.settings import SettingsUpdate


@pytest.fixture
def overrides_file(tmp_path, monkeypatch):
    # Run from an empty dir so a developer's .env and saved overrides don't leak in.
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "overrides.json"
    monkeypatch.setenv("SETTINGS_OVERRIDES_FILE", str(path))
    for key in ("TWITCH_CHANNELS", "VOD_MAX_PEAKS", "ENABLE_SUMMARIES", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_every_editable_setting_is_in_the_catalog() -> None:
    assert set(runtime_settings.SPECS_BY_KEY) == set(Settings.model_fields) - ENV_ONLY_SETTINGS
    group_ids = {group.id for group in runtime_settings.GROUPS}
    assert all(spec.group in group_ids for spec in runtime_settings.SETTING_SPECS)


def test_read_overrides_drops_unknown_and_env_only_keys(tmp_path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"vod_max_peaks": 5, "clickhouse_host": "evil", "nope": 1}))

    assert read_overrides(path) == {"vod_max_peaks": 5}


def test_read_overrides_ignores_corrupt_file(tmp_path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text("{not json")

    assert read_overrides(path) == {}


def test_overrides_take_precedence_over_env(overrides_file, monkeypatch) -> None:
    monkeypatch.setenv("TWITCH_CHANNELS", "from_env")
    overrides_file.write_text(json.dumps({"twitch_channels": "from_ui"}))

    assert load_settings().twitch_channels == "from_ui"


def test_invalid_overrides_fall_back_to_env(overrides_file) -> None:
    overrides_file.write_text(json.dumps({"vod_max_peaks": 0}))

    assert load_settings().vod_max_peaks == 12


def test_save_overrides_coerces_persists_and_reports_changes(overrides_file) -> None:
    changed = runtime_settings.save_overrides({"vod_max_peaks": "7", "enable_summaries": False})

    assert changed == {"vod_max_peaks", "enable_summaries"}
    assert json.loads(overrides_file.read_text()) == {"enable_summaries": False, "vod_max_peaks": 7}
    assert get_settings().vod_max_peaks == 7

    changed = runtime_settings.save_overrides({"vod_max_peaks": None})

    assert changed == {"vod_max_peaks"}
    assert json.loads(overrides_file.read_text()) == {"enable_summaries": False}
    assert get_settings().vod_max_peaks == 12


def test_save_overrides_rejects_env_only_unknown_and_invalid(overrides_file) -> None:
    for update in ({"clickhouse_host": "x"}, {"api_auth_token": "x"}, {"not_a_setting": 1}, {"vod_max_peaks": 0}):
        with pytest.raises(runtime_settings.SettingsUpdateError):
            runtime_settings.save_overrides(update)
    assert not overrides_file.exists()


def test_describe_never_returns_secret_values() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-secret", clickhouse_password="pw")

    fields = {field.key: field for field in runtime_settings.describe(settings, overrides={}, startup=None)}

    assert fields["openai_api_key"].value is None
    assert fields["openai_api_key"].is_set is True
    assert fields["twitch_access_token"].value is None
    assert "sk-secret" not in json.dumps([field.model_dump() for field in fields.values()])
    assert not ENV_ONLY_SETTINGS & set(fields)
    assert fields["vod_max_peaks"].minimum == 1 and fields["vod_max_peaks"].maximum == 50


def test_describe_flags_restart_only_changes() -> None:
    startup = Settings(_env_file=None)
    current = Settings(_env_file=None, recent_message_limit=10, vod_max_peaks=3)

    fields = {field.key: field for field in runtime_settings.describe(current, overrides={}, startup=startup)}

    assert fields["recent_message_limit"].pending_restart is True
    assert fields["vod_max_peaks"].pending_restart is False


def test_require_feature_rejects_disabled_feature() -> None:
    dependency = routes.require_feature("enable_summaries", "Chat summaries")
    dependency(settings=Settings(_env_file=None))

    with pytest.raises(HTTPException) as exc:
        dependency(settings=Settings(_env_file=None, enable_summaries=False))
    assert exc.value.status_code == 403


class FakeIngestion:
    def __init__(self) -> None:
        self.restarts: list[Settings] = []

    async def restart(self, settings: Settings) -> None:
        self.restarts.append(settings)


@pytest.mark.anyio
async def test_update_settings_restarts_ingestion_only_for_ingestion_keys(overrides_file) -> None:
    ingestion = FakeIngestion()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ingestion=ingestion)))

    response = await routes.update_settings(request, SettingsUpdate(values={"vod_max_peaks": 4}))
    assert response.changed == ["vod_max_peaks"]
    assert response.ingestion_restarted is False
    assert ingestion.restarts == []

    response = await routes.update_settings(request, SettingsUpdate(values={"twitch_channels": "a,b"}))
    assert response.ingestion_restarted is True
    assert ingestion.restarts[0].channel_logins == ["a", "b"]


@pytest.mark.anyio
async def test_update_settings_returns_422_for_env_only(overrides_file) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(HTTPException) as exc:
        await routes.update_settings(request, SettingsUpdate(values={"kafka_chat_topic": "x"}))
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_ingestion_manager_restart_replaces_configured_channels() -> None:
    from app.ingestion.manager import IngestionManager

    async def noop(_value) -> None:
        return None

    channels: dict = {}
    manager = IngestionManager(channels=channels, on_message=noop, on_status=noop)

    await manager.start(Settings(_env_file=None, twitch_channels="a,b", enable_twitch_ingestion=False))
    assert list(channels) == ["a", "b"]
    assert manager.running is False

    await manager.restart(Settings(_env_file=None, twitch_channels="c", enable_twitch_ingestion=False))
    assert list(channels) == ["c"]
