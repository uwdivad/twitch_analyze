"""runtime_settings catalog helpers and overrides-file robustness."""

import json

import pytest

from app.core import runtime_settings
from app.core.config import Settings, load_settings, read_overrides


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _fields(settings: Settings, **kwargs) -> dict:
    kwargs.setdefault("overrides", {})
    kwargs.setdefault("startup", None)
    return {field.key: field for field in runtime_settings.describe(settings, **kwargs)}


def test_catalog_marks_audio_capture_settings_as_worker_applied() -> None:
    # The pending_restart tests below rely on these keys being worker-applied.
    assert runtime_settings.SPECS_BY_KEY["audio_capture_channels"].apply == "worker"
    assert runtime_settings.SPECS_BY_KEY["enable_audio_capture"].apply == "worker"
    assert runtime_settings.SPECS_BY_KEY["vod_max_peaks"].apply == "live"
    assert runtime_settings.SPECS_BY_KEY["twitch_channels"].apply == "ingestion"


def test_describe_flags_pending_restart_for_changed_worker_settings() -> None:
    startup = _settings(audio_capture_channels="", enable_audio_capture=False)
    current = _settings(audio_capture_channels="a,b", enable_audio_capture=True)

    fields = _fields(current, startup=startup)

    assert fields["audio_capture_channels"].pending_restart is True
    assert fields["enable_audio_capture"].pending_restart is True


def test_describe_does_not_flag_unchanged_worker_or_changed_live_and_ingestion_settings() -> None:
    startup = _settings(audio_capture_channels="a", vod_max_peaks=12, twitch_channels="x")
    current = _settings(audio_capture_channels="a", vod_max_peaks=4, twitch_channels="y")

    fields = _fields(current, startup=startup)

    assert fields["audio_capture_channels"].pending_restart is False
    # live settings apply on the next request; ingestion settings restart ingestion on save.
    assert fields["vod_max_peaks"].pending_restart is False
    assert fields["twitch_channels"].pending_restart is False


def test_describe_never_flags_pending_restart_without_startup_snapshot() -> None:
    fields = _fields(_settings(audio_capture_channels="a,b"), startup=None)

    assert not any(field.pending_restart for field in fields.values())


def test_describe_marks_overridden_keys_only() -> None:
    settings = _settings(vod_max_peaks=4, enable_summaries=False)

    fields = _fields(settings, overrides={"vod_max_peaks": 4, "enable_summaries": False})

    assert fields["vod_max_peaks"].overridden is True
    assert fields["enable_summaries"].overridden is True
    assert fields["vod_max_buckets"].overridden is False
    assert sum(field.overridden for field in fields.values()) == 2


def test_describe_reports_env_value_from_base_settings() -> None:
    fields = _fields(_settings(vod_max_peaks=4), overrides={"vod_max_peaks": 4}, base=_settings(vod_max_peaks=9))

    assert fields["vod_max_peaks"].value == 4
    assert fields["vod_max_peaks"].env_value == 9


def test_feature_flags_report_disabled_flags_as_false() -> None:
    flags = runtime_settings.feature_flags(_settings(enable_summaries=False, enable_vod_labels=False))

    assert set(flags) == set(runtime_settings.FEATURE_FLAGS)
    assert flags["summaries"] is False
    assert flags["vod_labels"] is False
    assert flags["vod_analysis"] is True
    assert flags["transcription"] is True


@pytest.mark.parametrize("payload", [["vod_max_peaks", 5], [{"vod_max_peaks": 5}], "vod_max_peaks", 5, None])
def test_read_overrides_ignores_non_object_top_level(tmp_path, payload) -> None:
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps(payload))

    assert read_overrides(path) == {}


def test_load_settings_survives_list_overrides_file(tmp_path, monkeypatch) -> None:
    # Run from an empty dir so a developer's .env doesn't leak in.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VOD_MAX_PEAKS", raising=False)
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps([{"vod_max_peaks": 5}]))
    monkeypatch.setenv("SETTINGS_OVERRIDES_FILE", str(path))

    settings = load_settings()

    assert settings.vod_max_peaks == Settings.model_fields["vod_max_peaks"].default
