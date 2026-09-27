import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Settings that can only come from the environment/.env, never from the UI overrides
# file: connection/topology values every process must agree on (and that compose
# injects per container), the API auth token (editing it through the API it guards
# could lock the dashboard out), and the overrides file location itself.
ENV_ONLY_SETTINGS = frozenset(
    {
        "api_auth_token",
        "settings_overrides_file",
        "log_dir",
        "kafka_bootstrap_servers",
        "kafka_chat_topic",
        "kafka_transcript_topic",
        "kafka_consumer_group",
        "kafka_transcript_consumer_group",
        "clickhouse_host",
        "clickhouse_port",
        "clickhouse_username",
        "clickhouse_password",
        "clickhouse_database",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    # Each process writes <log_dir>/<process>.log, rotated at UTC midnight.
    log_dir: str = "logs"
    log_retention_days: int = Field(default=14, ge=1)
    api_auth_token: str = ""
    # JSON file holding values saved from the dashboard Settings page. They take
    # precedence over the environment and .env. Relative paths resolve from the cwd.
    settings_overrides_file: str = "data/settings-overrides.json"

    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    twitch_access_token: str = ""
    twitch_refresh_token: str = ""
    twitch_user_id: str = ""
    twitch_username: str = ""
    twitch_channels: str = ""
    twitch_ingestion_mode: str = "irc"

    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_chat_topic: str = "twitch.chat.messages"
    kafka_transcript_topic: str = "twitch.stream.transcripts"
    kafka_consumer_group: str = "clickhouse-chat-writer"
    kafka_transcript_consumer_group: str = "clickhouse-transcript-writer"

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_username: str = "default"
    clickhouse_password: str = "twitch_analyze"
    clickhouse_database: str = "twitch_analyze"

    recent_message_limit: int = Field(default=500, ge=1, le=10_000)
    enable_twitch_ingestion: bool = True
    cors_origins: str = "http://localhost:5173"
    openai_api_key: str = ""
    openai_summary_model: str = "gpt-5.2"
    openai_summary_max_messages: int = Field(default=250, ge=25, le=1000)
    openai_timeout_seconds: float = 60.0
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    transcription_max_concurrent_jobs: int = 3
    enable_audio_capture: bool = False
    audio_capture_channels: str = ""
    audio_segment_seconds: int = Field(default=30, ge=5, le=300)
    audio_retention_minutes: int = Field(default=60, ge=1, le=1440)
    audio_chunk_dir: str = "/tmp/twitch-audio"

    twitch_gql_url: str = "https://gql.twitch.tv/gql"
    # Not the browser web client id (kimne78...): Twitch rejects cursor-paged chat
    # replay requests from that id with "failed integrity check". This public id is
    # the one TwitchDownloader uses for chat replay and pages by cursor without it.
    twitch_gql_client_id: str = "kd1unb4b3q4t58fwlpcbzcbnm76a8fp"
    twitch_gql_comments_query_hash: str = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
    vod_max_concurrent_jobs: int = Field(default=2, ge=1, le=10)
    vod_fetch_page_delay_seconds: float = 0.1
    vod_fetch_max_retries: int = 5
    vod_max_comments: int = 500_000
    vod_max_buckets: int = 720
    vod_max_peaks: int = Field(default=12, ge=1, le=50)
    vod_catchup_timeout_seconds: float = 900.0
    vod_catchup_poll_seconds: float = 2.0
    vod_catchup_stable_polls: int = 5
    # Empty falls back to openai_summary_model.
    openai_vod_label_model: str = ""

    # Feature flags (dashboard features; the ingestion/audio toggles live above).
    enable_summaries: bool = True
    enable_transcription: bool = True
    enable_vod_analysis: bool = True
    enable_vod_labels: bool = True

    @property
    def channel_logins(self) -> list[str]:
        return [part.strip().lower() for part in self.twitch_channels.split(",") if part.strip()]

    @property
    def audio_channel_logins(self) -> list[str]:
        channels = self.audio_capture_channels or self.twitch_channels
        return [part.strip().lower() for part in channels.split(",") if part.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]

    @property
    def twitch_configured(self) -> bool:
        if self.twitch_ingestion_mode == "irc":
            return bool(self.channel_logins)
        return bool(self.twitch_client_id and self.twitch_access_token and self.channel_logins)

    @property
    def twitch_irc_anonymous(self) -> bool:
        return not (self.twitch_access_token and self.twitch_username)

    @field_validator("clickhouse_password", mode="before")
    @classmethod
    def default_clickhouse_password(cls, value: str | None) -> str:
        # Only apply the default when the value is truly unset; an explicitly
        # empty password (e.g. CLICKHOUSE_PASSWORD="") must stay empty.
        if value is None:
            return "twitch_analyze"
        return value


def read_overrides(path: str | Path) -> dict[str, Any]:
    """Load saved UI overrides, keeping only known, editable settings."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Ignoring unreadable settings overrides file %s", path, exc_info=True)
        return {}
    if not isinstance(raw, dict):
        logger.warning("Ignoring settings overrides file %s: expected a JSON object", path)
        return {}
    return {
        key: value
        for key, value in raw.items()
        if key in Settings.model_fields and key not in ENV_ONLY_SETTINGS
    }


def load_settings() -> Settings:
    base = Settings()
    overrides = read_overrides(base.settings_overrides_file)
    if not overrides:
        return base
    try:
        # Init kwargs take precedence over env/.env in pydantic-settings.
        return Settings(**overrides)
    except ValidationError:
        logger.warning("Ignoring invalid settings overrides in %s", base.settings_overrides_file, exc_info=True)
        return base


@lru_cache
def get_settings() -> Settings:
    # Cached per process; the settings API clears the cache after saving overrides.
    return load_settings()
