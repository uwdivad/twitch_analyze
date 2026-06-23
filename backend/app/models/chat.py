from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ChannelInfo(BaseModel):
    channel_id: str
    channel_login: str
    channel_display_name: str
    status: str = "configured"
    detail: str | None = None


class ChatMessage(BaseModel):
    message_id: str
    eventsub_message_id: str = ""
    channel_id: str
    channel_login: str
    channel_display_name: str
    session_id: str
    session_date: date
    chatter_user_id: str
    chatter_login: str
    chatter_display_name: str
    message_text: str
    message_fragments: list[dict[str, Any]] = Field(default_factory=list)
    badges: list[dict[str, Any]] = Field(default_factory=list)
    emotes: list[dict[str, Any]] = Field(default_factory=list)
    mentions: list[dict[str, Any]] = Field(default_factory=list)
    reply: dict[str, Any] | None = None
    raw_event: dict[str, Any] = Field(default_factory=dict)
    event_ts: datetime
    received_at: datetime = Field(default_factory=utc_now)


class LiveEnvelope(BaseModel):
    type: str
    payload: dict[str, Any] | ChatMessage | list[ChatMessage] | list[ChannelInfo]


class MessageQuery(BaseModel):
    channel: str | None = None
    session_id: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)


class VolumePoint(BaseModel):
    bucket: datetime
    message_count: int
    unique_chatter_count: int


class MessageTotal(BaseModel):
    count: int


class InsertResult(BaseModel):
    inserted: int


class TopItem(BaseModel):
    value: str
    count: int


class SpikeWindow(BaseModel):
    bucket: datetime
    message_count: int
    unique_chatter_count: int


class SummarySourceStats(BaseModel):
    message_count: int
    unique_chatter_count: int
    top_chatters: list[TopItem] = Field(default_factory=list)
    top_emotes: list[TopItem] = Field(default_factory=list)
    spike_windows: list[SpikeWindow] = Field(default_factory=list)
    sampled_message_count: int = 0


class SummaryContext(BaseModel):
    channel_id: str
    channel_login: str
    channel_display_name: str
    session_id: str
    window_start: datetime
    window_end: datetime
    window_size: str
    stats: SummarySourceStats
    sample_messages: list[ChatMessage] = Field(default_factory=list)


class ChatSummary(BaseModel):
    summary_id: str
    channel_id: str
    channel_login: str
    session_id: str
    window_start: datetime
    window_end: datetime
    window_size: str
    summary_text: str
    model: str
    source_stats: SummarySourceStats
    created_at: datetime


class GenerateSummaryRequest(BaseModel):
    channel: str
    window_minutes: int = Field(default=60, ge=1, le=1440)


class TranscriptSegment(BaseModel):
    segment_id: str
    channel_login: str
    session_id: str
    segment_started_at: datetime
    segment_ended_at: datetime
    audio_path: str
    transcript_text: str = ""
    model: str
    status: str
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class StartTranscriptionRequest(BaseModel):
    channel: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    duration_minutes: int = Field(default=5, ge=1, le=180)


class TranscriptionJob(BaseModel):
    job_id: str
    channel_login: str
    duration_seconds: int
    status: str
    started_at: datetime
    ends_at: datetime
    detail: str = ""
