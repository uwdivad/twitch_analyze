"""Dashboard-editable settings: the catalog the Settings page renders, and the
overrides file it saves to.

Every `Settings` field except `ENV_ONLY_SETTINGS` appears in `SETTING_SPECS` (a test
enforces this), so a new setting must be described here before it shows up on the page.
Env-only settings are left out of the page entirely.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from annotated_types import Ge, Gt, Le, Lt
from pydantic import ValidationError

from app.core.config import ENV_ONLY_SETTINGS, Settings, get_settings, read_overrides
from app.models.settings import SettingField, SettingGroup

# When a saved change takes effect:
# - live: read per request/job, applies on the next one.
# - ingestion: the API restarts Twitch ingestion right after saving.
# - restart: read once at API startup; restart the backend.
# - worker: read by a standalone worker process; restart that worker.
ApplyMode = Literal["live", "ingestion", "restart", "worker"]
SettingKind = Literal["string", "secret", "int", "float", "bool", "select", "list"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    group: str
    label: str
    description: str
    kind: SettingKind
    apply: ApplyMode
    options: tuple[str, ...] = ()


GROUPS: tuple[SettingGroup, ...] = (
    SettingGroup(id="features", label="Feature flags", description="Turn whole features on or off."),
    SettingGroup(id="twitch", label="Twitch ingestion", description="Which channels are read and how."),
    SettingGroup(id="openai", label="OpenAI", description="Models and limits for summaries, labels and transcription."),
    SettingGroup(id="transcription", label="Transcription & audio", description="Audio capture and speech-to-text."),
    SettingGroup(id="vod", label="VOD analysis", description="Chat-replay import and peak detection."),
    SettingGroup(id="app", label="App", description="API process behavior."),
)

SETTING_SPECS: tuple[SettingSpec, ...] = (
    # Feature flags
    SettingSpec("enable_twitch_ingestion", "features", "Twitch ingestion",
                "Read live chat from the configured channels.", "bool", "ingestion"),
    SettingSpec("enable_summaries", "features", "Chat summaries",
                "Allow generating OpenAI chat summaries from the dashboard.", "bool", "live"),
    SettingSpec("enable_transcription", "features", "Timed transcription",
                "Allow starting timed stream-audio transcription jobs.", "bool", "live"),
    SettingSpec("enable_vod_analysis", "features", "VOD analysis",
                "Allow importing and analyzing VOD chat replays. Stored analyses stay viewable.", "bool", "live"),
    SettingSpec("enable_vod_labels", "features", "AI peak labels",
                "Allow titling VOD chat peaks with OpenAI.", "bool", "live"),
    SettingSpec("enable_audio_capture", "features", "Continuous audio capture",
                "Run the standalone audio-capture worker for the audio channels.", "bool", "worker"),
    # Twitch
    SettingSpec("twitch_channels", "twitch", "Channels",
                "Comma-separated channel logins to ingest.", "list", "ingestion"),
    SettingSpec("twitch_ingestion_mode", "twitch", "Ingestion mode",
                "IRC reads public chat anonymously; EventSub needs a client id and user token.",
                "select", "ingestion", ("irc", "eventsub")),
    SettingSpec("twitch_username", "twitch", "Username",
                "Login used for authenticated IRC. Leave empty for anonymous IRC.", "string", "ingestion"),
    SettingSpec("twitch_access_token", "twitch", "Access token",
                "User access token for authenticated IRC or EventSub.", "secret", "ingestion"),
    SettingSpec("twitch_client_id", "twitch", "Client id",
                "Twitch application client id (EventSub).", "string", "ingestion"),
    SettingSpec("twitch_client_secret", "twitch", "Client secret",
                "Twitch application client secret.", "secret", "ingestion"),
    SettingSpec("twitch_refresh_token", "twitch", "Refresh token",
                "User refresh token.", "secret", "ingestion"),
    SettingSpec("twitch_user_id", "twitch", "User id",
                "EventSub user id; resolved from the token when empty.", "string", "ingestion"),
    # OpenAI
    SettingSpec("openai_api_key", "openai", "API key",
                "Required for summaries, AI peak labels and transcription.", "secret", "live"),
    SettingSpec("openai_summary_model", "openai", "Summary model",
                "Model used for chat summaries.", "string", "live"),
    SettingSpec("openai_summary_max_messages", "openai", "Summary sample size",
                "Max sampled messages sent with a summary request.", "int", "live"),
    SettingSpec("openai_vod_label_model", "openai", "VOD label model",
                "Model for AI peak titles. Empty uses the summary model.", "string", "live"),
    SettingSpec("openai_timeout_seconds", "openai", "Request timeout (s)",
                "Timeout for summary and label requests.", "float", "live"),
    SettingSpec("openai_transcription_model", "openai", "Transcription model",
                "Speech-to-text model.", "string", "live"),
    # Transcription & audio
    SettingSpec("transcription_max_concurrent_jobs", "transcription", "Max concurrent jobs",
                "Further transcription starts are rejected.", "int", "live"),
    SettingSpec("audio_capture_channels", "transcription", "Audio channels",
                "Channels for continuous capture. Empty uses the ingestion channels.", "list", "worker"),
    SettingSpec("audio_segment_seconds", "transcription", "Segment length (s)",
                "Length of each captured audio chunk.", "int", "live"),
    SettingSpec("audio_retention_minutes", "transcription", "Chunk retention (min)",
                "How long captured audio chunks stay on disk.", "int", "live"),
    SettingSpec("audio_chunk_dir", "transcription", "Chunk directory",
                "Where audio chunks are written.", "string", "live"),
    # VOD analysis
    SettingSpec("vod_max_concurrent_jobs", "vod", "Max concurrent jobs",
                "Further VOD analysis starts are rejected.", "int", "live"),
    SettingSpec("vod_max_comments", "vod", "Max comments per VOD",
                "Hard cap on fetched chat-replay comments.", "int", "live"),
    SettingSpec("vod_max_buckets", "vod", "Max activity buckets",
                "Bucket size grows to stay under this.", "int", "live"),
    SettingSpec("vod_max_peaks", "vod", "Max peaks",
                "Chat peaks reported per VOD.", "int", "live"),
    SettingSpec("vod_fetch_page_delay_seconds", "vod", "Page delay (s)",
                "Delay between chat-replay page fetches.", "float", "live"),
    SettingSpec("vod_fetch_max_retries", "vod", "Page retries",
                "Retries per page before the job fails.", "int", "live"),
    SettingSpec("vod_catchup_timeout_seconds", "vod", "Catch-up timeout (s)",
                "Max wait for ClickHouse to store the imported chat.", "float", "live"),
    SettingSpec("vod_catchup_poll_seconds", "vod", "Catch-up poll (s)",
                "Seconds between ClickHouse catch-up polls.", "float", "live"),
    SettingSpec("vod_catchup_stable_polls", "vod", "Stable polls",
                "Unchanged polls required before analysis starts.", "int", "live"),
    SettingSpec("twitch_gql_url", "vod", "GQL URL",
                "Twitch web GQL endpoint.", "string", "live"),
    SettingSpec("twitch_gql_client_id", "vod", "GQL client id",
                "Public client id for chat replay (the browser web id fails the integrity check).",
                "string", "live"),
    SettingSpec("twitch_gql_comments_query_hash", "vod", "Comments query hash",
                "Persisted-query hash for VideoCommentsByOffsetOrCursor.", "string", "live"),
    # App
    SettingSpec("recent_message_limit", "app", "Live buffer size",
                "Messages kept in memory for the live feed fallback.", "int", "restart"),
    SettingSpec("cors_origins", "app", "CORS origins",
                "Comma-separated browser origins allowed to call the API.", "list", "restart"),
    SettingSpec("log_level", "app", "Log level", "Backend log verbosity.",
                "select", "restart", ("DEBUG", "INFO", "WARNING", "ERROR")),
    SettingSpec("log_retention_days", "app", "Log retention (days)",
                "Daily log files kept per process before the oldest is deleted.", "int", "restart"),
    SettingSpec("app_env", "app", "Environment", "Deployment environment label.", "string", "restart"),
)

SPECS_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SETTING_SPECS}
INGESTION_SETTINGS = frozenset(spec.key for spec in SETTING_SPECS if spec.apply == "ingestion")
# Values the frontend needs to show/hide features; exposed unauthenticated.
FEATURE_FLAGS: dict[str, str] = {
    "twitch_ingestion": "enable_twitch_ingestion",
    "summaries": "enable_summaries",
    "transcription": "enable_transcription",
    "vod_analysis": "enable_vod_analysis",
    "vod_labels": "enable_vod_labels",
    "audio_capture": "enable_audio_capture",
}


class SettingsUpdateError(ValueError):
    """An update named an unknown or env-only setting, or failed validation."""


def _bounds(key: str) -> tuple[float | None, float | None]:
    minimum = maximum = None
    for item in Settings.model_fields[key].metadata:
        if isinstance(item, Ge):
            minimum = item.ge
        elif isinstance(item, Gt):
            minimum = item.gt
        elif isinstance(item, Le):
            maximum = item.le
        elif isinstance(item, Lt):
            maximum = item.lt
    return minimum, maximum


def _env_var(key: str) -> str:
    return key.upper()


def describe(
    settings: Settings,
    overrides: dict[str, Any],
    startup: Settings | None,
    base: Settings | None = None,
) -> list[SettingField]:
    """Build the Settings page rows. Secret values are never returned, only whether set."""
    fields: list[SettingField] = []
    for spec in SETTING_SPECS:
        value = getattr(settings, spec.key)
        default = Settings.model_fields[spec.key].default
        minimum, maximum = _bounds(spec.key)
        secret = spec.kind == "secret"
        needs_restart = (
            startup is not None
            and spec.apply in ("restart", "worker")
            and getattr(startup, spec.key) != value
        )
        fields.append(
            SettingField(
                key=spec.key,
                env_var=_env_var(spec.key),
                group=spec.group,
                label=spec.label,
                description=spec.description,
                kind=spec.kind,
                apply=spec.apply,
                options=list(spec.options),
                minimum=minimum,
                maximum=maximum,
                secret=secret,
                value=None if secret else value,
                default=None if secret else default,
                env_value=None if secret or base is None else getattr(base, spec.key),
                is_set=bool(value) if secret else value not in ("", None),
                overridden=spec.key in overrides,
                pending_restart=needs_restart,
            )
        )
    return fields


def feature_flags(settings: Settings) -> dict[str, bool]:
    return {name: bool(getattr(settings, key)) for name, key in FEATURE_FLAGS.items()}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_overrides(updates: dict[str, Any]) -> set[str]:
    """Apply `updates` to the overrides file and return the keys whose effective value changed.

    A value of None removes the override, falling back to the environment/.env value.
    The merged result is validated as a whole before anything is written.
    """
    for key in updates:
        if key in ENV_ONLY_SETTINGS:
            raise SettingsUpdateError(f"{_env_var(key)} can only be set in the environment/.env")
        if key not in SPECS_BY_KEY:
            raise SettingsUpdateError(f"Unknown setting: {key}")

    before = get_settings()
    path = Path(before.settings_overrides_file)
    overrides = read_overrides(path)
    for key, value in updates.items():
        if value is None:
            overrides.pop(key, None)
        else:
            overrides[key] = value

    try:
        validated = Settings(**overrides)
    except ValidationError as exc:
        messages = "; ".join(
            f"{_env_var(str(error['loc'][0])) if error['loc'] else 'settings'}: {error['msg']}"
            for error in exc.errors()
        )
        raise SettingsUpdateError(messages) from exc

    # Store the coerced values ("5" -> 5, "true" -> True) so the file stays typed.
    _write_json_atomic(path, {key: getattr(validated, key) for key in overrides})
    get_settings.cache_clear()
    after = get_settings()
    return {key for key in SPECS_BY_KEY if getattr(before, key) != getattr(after, key)}
