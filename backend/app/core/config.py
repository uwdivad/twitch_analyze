from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

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
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    enable_audio_capture: bool = False
    audio_capture_channels: str = ""
    audio_segment_seconds: int = Field(default=30, ge=5, le=300)
    audio_retention_minutes: int = Field(default=60, ge=1, le=1440)
    audio_chunk_dir: str = "/tmp/twitch-audio"

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
        if value is None or value == "":
            return "twitch_analyze"
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
